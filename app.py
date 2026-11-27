import asyncio
import socket
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, Query, Body, Request, Response, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.airports import (
    POLISH_AIRPORTS,
    WORLD_PRESETS,
    CITY_SUGGESTIONS,
    resolve_agoda_search_url,
    search_world_airports_live,
)
from backend.holidays_pl import get_upcoming_polish_holidays
from backend.comparator import scan_and_compare_flights
from backend.providers.calendar_engine import get_universal_monthly_calendar
from backend.providers.deals_rss import fetch_promo_rss_deals
from backend.providers.amadeus_gds import is_amadeus_configured
from backend.providers.travelpayouts import is_travelpayouts_configured
from backend.database import (
    init_db,
    list_watchlist,
    add_watchlist_item,
    delete_watchlist_item,
    list_saved_deals,
    save_deal_for_group,
    vote_saved_deal,
    delete_saved_deal,
    get_public_settings,
    save_settings,
    check_squad_pin,
    mark_watchlist_checked,
    get_route_history_summary,
)
from backend.notifier import send_deals_notification
from backend.security import (
    create_session_token,
    verify_session_token,
    rate_limiter,
    get_client_ip,
)

app = FastAPI(
    title="SkyPulse.pl – Aviation Radar & Boarding Pass System",
    version="3.0.0",
    docs_url=None,
    redoc_url=None,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

AUTOSCANNER_STATE: Dict[str, Any] = {
    "enabled": True,
    "interval_minutes": 60,
    "last_run_at": None,
    "last_deals_found": 0,
    "runs_count": 0,
}


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_current_session(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get("skypulse_session") or request.headers.get("x-squad-token")
    return verify_session_token(token)


async def execute_watchlist_autoscan(send_notifications: bool = True) -> Dict[str, Any]:
    items = list_watchlist()
    today = datetime.now().date()
    d_from = (today + timedelta(days=3)).isoformat()
    d_to = (today + timedelta(days=42)).isoformat()
    total_found = 0

    for w in items[:5]:
        try:
            res = await scan_and_compare_flights(
                airports=[a.strip() for a in w["airports"].split(",") if a.strip()],
                date_from=d_from,
                date_to=d_to,
                trip_type=w["trip_type"],
                nights_min=w["nights_min"],
                nights_max=w["nights_max"],
                max_price=int(w["max_price_pln"]),
                dest_query=w["dest_query"],
                group_size=w["group_size"],
            )
            flights = res.get("flights") or []
            total_found += len(flights)
            if flights:
                flights.sort(key=lambda x: x["price_pln"])
                best_deal = flights[0]
                best_price = float(best_deal["price_pln"])
                best_route = f"{best_deal['origin_iata']}->{best_deal['dest_iata']}:{(best_deal.get('outbound_departure') or '')[:10]}"
                prev_price = w.get("last_notified_price")
                prev_route = w.get("last_notified_route") or ""

                # Anti-spam: notify only on first hit, price drop >= 5 PLN, or new equal/cheaper route
                should_notify = (
                    prev_price is None
                    or best_price <= float(prev_price) - 5.0
                    or (best_route != prev_route and best_price <= float(prev_price))
                )
                if send_notifications and should_notify:
                    await send_deals_notification(
                        flights[:5],
                        title=f"NOWY SPADEK CENY: {w['title']} (od {round(best_price)} zł)",
                        group_size=w["group_size"],
                    )
                    mark_watchlist_checked(w["id"], notified_price=best_price, notified_route=best_route)
                else:
                    mark_watchlist_checked(w["id"])
            else:
                mark_watchlist_checked(w["id"])
        except Exception:
            continue

    AUTOSCANNER_STATE["last_run_at"] = datetime.now().isoformat(timespec="seconds")
    AUTOSCANNER_STATE["last_deals_found"] = total_found
    AUTOSCANNER_STATE["runs_count"] += 1
    return AUTOSCANNER_STATE


async def _background_autoscanner_loop() -> None:
    await asyncio.sleep(20)
    while True:
        if AUTOSCANNER_STATE.get("enabled"):
            try:
                await execute_watchlist_autoscan(send_notifications=True)
            except Exception:
                pass
        await asyncio.sleep(AUTOSCANNER_STATE.get("interval_minutes", 60) * 60)


@app.middleware("http")
async def security_and_rate_limit_middleware(request: Request, call_next):
    client_ip = get_client_ip(request)
    path = request.url.path
    ua = (request.headers.get("user-agent") or "").lower()

    # Block site-copying and scraping bots
    blocked_bots = ("httrack", "webcopier", "teleport", "sitesucker", "scrapy", "Offline Explorer".lower(), "nikto", "sqlmap")
    if any(b in ua for b in blocked_bots):
        return JSONResponse(status_code=403, content={"error": "Access denied."})

    if path.startswith("/api/") or path.startswith("/go/"):
        limit = 25 if path.startswith("/api/search") else 100
        allowed, retry_after = rate_limiter.check_rate_limit(
            f"{client_ip}:{path[:12]}", max_requests=limit, window_seconds=60.0
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"error": f"Zbyt wiele zapytań. Odczekaj {retry_after} sek."},
                headers={"Retry-After": str(retry_after)},
            )

        pub_settings = get_public_settings()
        if pub_settings.get("require_pin") and path not in ("/api/info", "/api/auth/login", "/api/auth/me"):
            sess = get_current_session(request)
            if not sess:
                return JSONResponse(
                    status_code=401,
                    content={"error": "Wymagany kod dostępu Ekipy (PIN).", "auth_required": True},
                )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    if path.startswith("/api/") or path.startswith("/go/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    elif path in ("/", "/static/app.bundle.js"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    asyncio.create_task(_background_autoscanner_loop())


@app.post("/api/auth/login")
def api_login(request: Request, response: Response, payload: Dict[str, Any] = Body(...)):
    ip = get_client_ip(request)
    locked, wait_sec = rate_limiter.is_login_locked(ip)
    if locked:
        raise HTTPException(
            status_code=429,
            detail=f"Zbyt wiele błędnych prób logowania. Blokada na {wait_sec} s.",
        )

    pin = str(payload.get("pin") or "").strip()
    nick = str(payload.get("nick") or "Znajomy").strip()[:25] or "Znajomy"

    if not check_squad_pin(pin):
        rate_limiter.record_failed_login(ip)
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod dostępu Ekipy (PIN).")

    rate_limiter.clear_failed_logins(ip)
    token = create_session_token(nick=nick, role="admin")
    response.set_cookie(
        key="skypulse_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 14,
    )
    return {"ok": True, "nick": nick, "token": token}


@app.post("/api/auth/logout")
def api_logout(response: Response):
    response.delete_cookie("skypulse_session")
    return {"ok": True}


@app.get("/health")
def health_check() -> Dict[str, str]:
    """Lightweight unauthenticated liveness endpoint for uptime and container probes."""
    return {"status": "ok", "service": "skypulse", "version": app.version}


@app.get("/api/info")
def api_info(request: Request) -> Dict[str, Any]:
    lan_ip = get_lan_ip()
    today = datetime.now().date()
    default_from = (today + timedelta(days=3)).isoformat()
    default_to = (today + timedelta(days=38)).isoformat()
    pub_settings = get_public_settings()
    sess = get_current_session(request)

    engines = [
        "Ryanair Direct API",
        "Kiwi Multi-Airline (Koszyki Regionalne)",
        "Składak 2× One-Way (Bez marży)",
        "Czartery i Okazje Dnia (Fly4free + MlecznePodróże + WakacyjniPiraci)",
    ]
    if is_amadeus_configured():
        engines.append("Amadeus Self-Service GDS API")
    if is_travelpayouts_configured():
        engines.append("Travelpayouts / Aviasales API")

    return {
        "airports": POLISH_AIRPORTS,
        "world_presets": WORLD_PRESETS,
        "city_suggestions": CITY_SUGGESTIONS,
        "holiday_presets": get_upcoming_polish_holidays(limit=6),
        "lan_ip": lan_ip,
        "mobile_lan_url": f"http://{lan_ip}:8000",
        "default_date_from": default_from,
        "default_date_to": default_to,
        "settings": pub_settings,
        "autoscanner": AUTOSCANNER_STATE,
        "engines": engines,
        "authenticated": bool(sess) or (not pub_settings.get("require_pin")),
        "user_nick": sess["nick"] if sess else "Ekipa",
    }


@app.get("/api/airports/search")
async def api_airports_search(
    q: str = Query(""),
    limit: int = Query(12, ge=1, le=25),
    include_presets: bool = Query(True),
) -> Dict[str, Any]:
    return {
        "results": await search_world_airports_live(q, limit=limit, include_presets=include_presets)
    }


@app.get("/api/search")
async def api_search(
    airports: str = Query("WAW,WMI,KRK,KTW,GDN,WRO,POZ,RZE,LCJ,SZZ,BZG,LUZ"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    trip_type: str = Query("roundtrip"),
    nights_min: int = Query(2, ge=1, le=30),
    nights_max: int = Query(12, ge=1, le=30),
    max_price: Optional[int] = Query(2800),
    dest_query: Optional[str] = Query("WORLD"),
    group_size: int = Query(4, ge=1, le=20),
    max_stops: int = Query(2, ge=0, le=3),
    max_layover_hours: int = Query(6, ge=1, le=30),
    max_travel_hours: int = Query(26, ge=2, le=65),
    sort_by: str = Query("price"),
    weekend_only: bool = Query(False),
    warm_only: bool = Query(False),
    comfortable_hours: bool = Query(False),
    baggage_mode: str = Query("backpack"),
    sources: str = Query("all"),
) -> Dict[str, Any]:
    today = datetime.now().date()
    if not date_from:
        date_from = (today + timedelta(days=3)).isoformat()
    if not date_to:
        date_to = (today + timedelta(days=38)).isoformat()

    airport_list = [a.strip().upper() for a in airports.split(",") if a.strip()]
    return await scan_and_compare_flights(
        airports=airport_list,
        date_from=date_from,
        date_to=date_to,
        trip_type=trip_type,
        nights_min=nights_min,
        nights_max=nights_max,
        max_price=max_price,
        dest_query=dest_query,
        group_size=group_size,
        max_stops=max_stops,
        max_layover_hours=max_layover_hours,
        max_travel_hours=max_travel_hours,
        sort_by=sort_by,
        weekend_only=weekend_only,
        warm_only=warm_only,
        comfortable_hours=comfortable_hours,
        baggage_mode=baggage_mode,
        sources=sources,
    )


@app.get("/api/calendar")
async def api_calendar(
    origin_iata: str = Query("ALL"),
    dest_iata: str = Query("IT"),
    month: Optional[str] = Query(None),
) -> Dict[str, Any]:
    if not month:
        next_m = datetime.now().date() + timedelta(days=15)
        month = next_m.strftime("%Y-%m")
    return await get_universal_monthly_calendar(origin_iata, dest_iata, month)


@app.get("/api/promo-feeds")
async def api_promo_feeds(
    q: str = Query(""),
    category: str = Query("all"),
) -> Dict[str, Any]:
    return await fetch_promo_rss_deals(query=q, category=category)


@app.get("/api/route-history")
def api_route_history(limit: int = Query(24, ge=1, le=60)) -> Dict[str, Any]:
    return {"routes": get_route_history_summary(limit=limit)}


@app.get("/api/watchlist")
def api_get_watchlist():
    return {"items": list_watchlist(), "autoscanner": AUTOSCANNER_STATE}


@app.post("/api/watchlist")
def api_add_watchlist(payload: Dict[str, Any] = Body(...)):
    item = add_watchlist_item(payload)
    return {"item": item}


@app.delete("/api/watchlist/{item_id}")
def api_delete_watchlist(item_id: int):
    delete_watchlist_item(item_id)
    return {"ok": True}


@app.post("/api/watchlist/{item_id}/run")
async def api_run_watchlist(item_id: int, notify: bool = Query(False)):
    items = [x for x in list_watchlist() if x["id"] == item_id]
    if not items:
        return {"error": "Nie znaleziono alertu"}
    w = items[0]
    today = datetime.now().date()
    d_from = (today + timedelta(days=3)).isoformat()
    d_to = (today + timedelta(days=45)).isoformat()
    res = await scan_and_compare_flights(
        airports=[a.strip() for a in w["airports"].split(",") if a.strip()],
        date_from=d_from,
        date_to=d_to,
        trip_type=w["trip_type"],
        nights_min=w["nights_min"],
        nights_max=w["nights_max"],
        max_price=int(w["max_price_pln"]),
        dest_query=w["dest_query"],
        group_size=w["group_size"],
    )
    notif_res = None
    if notify and res["flights"]:
        notif_res = await send_deals_notification(
            res["flights"],
            title=f"Alert SkyPulse: {w['title']}",
            group_size=w["group_size"],
        )
    return {"scan": res, "notification": notif_res}


@app.post("/api/autoscanner/run-now")
async def api_run_autoscanner_now():
    state = await execute_watchlist_autoscan(send_notifications=True)
    return {"autoscanner": state}


@app.get("/api/saved")
def api_get_saved(request: Request, voter_id: str = Query("")):
    vid = voter_id or request.cookies.get("skypulse_voter_id") or get_client_ip(request)
    return {"deals": list_saved_deals(voter_id=vid)}


@app.post("/api/saved")
def api_save_deal(request: Request, payload: Dict[str, Any] = Body(...)):
    deal = payload.get("deal") or payload
    group_size = int(payload.get("group_size") or deal.get("group_size") or 4)
    note = payload.get("note") or ""
    sess = get_current_session(request)
    nick = payload.get("nick") or (sess["nick"] if sess else "Znajomy")
    vid = str(payload.get("voter_id") or request.cookies.get("skypulse_voter_id") or get_client_ip(request))
    saved = save_deal_for_group(deal, group_size=group_size, note=note, voter_nick=nick, voter_id=vid)
    return {"saved": saved}


@app.post("/api/saved/{deal_db_id}/vote")
def api_vote_saved(
    request: Request,
    deal_db_id: int,
    delta: int = Query(1),
    nick: str = Query(""),
    voter_id: str = Query(""),
):
    sess = get_current_session(request)
    voter = nick or (sess["nick"] if sess else "Znajomy")
    vid = voter_id or request.cookies.get("skypulse_voter_id") or get_client_ip(request)
    clean_delta = 1 if delta >= 0 else -1
    vote_info = vote_saved_deal(deal_db_id, clean_delta, voter_nick=voter, voter_id=vid)
    return {"deals": list_saved_deals(voter_id=vid), **vote_info}


@app.delete("/api/saved/{deal_db_id}")
def api_delete_saved(deal_db_id: int):
    delete_saved_deal(deal_db_id)
    return {"deals": list_saved_deals()}


@app.post("/api/settings")
def api_save_settings(payload: Dict[str, Any] = Body(...)):
    updated = save_settings(payload)
    return {"settings": updated}


@app.post("/api/notify")
async def api_notify(payload: Dict[str, Any] = Body(...)):
    deals = payload.get("deals") or []
    title = payload.get("title") or "SkyPulse.pl – Okazje znalezione dla Ekipy"
    group_size = int(payload.get("group_size") or 4)
    return await send_deals_notification(deals, title=title, group_size=group_size)


@app.get("/go/agoda")
async def go_agoda(
    city: str = Query("Europe"),
    checkin: str = Query(""),
    checkout: str = Query(""),
    adults: int = Query(2, ge=1, le=20),
):
    target_url = await resolve_agoda_search_url(city, checkin, checkout, adults)
    return RedirectResponse(url=target_url, status_code=302)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/favicon.ico")
def serve_favicon():
    return FileResponse(str(STATIC_DIR / "favicon.ico"), media_type="image/x-icon")


@app.get("/apple-touch-icon.png")
def serve_apple_touch_icon():
    return FileResponse(str(STATIC_DIR / "apple-touch-icon.png"), media_type="image/png")


@app.get("/manifest.json")
def serve_manifest():
    return FileResponse(str(STATIC_DIR / "manifest.json"))


@app.get("/")
def serve_index():
    return FileResponse(str(STATIC_DIR / "index.html"))
