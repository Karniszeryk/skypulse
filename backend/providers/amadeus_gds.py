import asyncio
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from backend.airports import (
    build_booking_links,
    country_code_to_flag,
    estimate_distance_km,
    format_duration_hm,
    get_airport_meta,
)
from backend.database import get_settings

_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?")
_TOKEN_CACHE: Dict[str, Any] = {"token": "", "exp": 0.0, "base": ""}
_SEARCH_CACHE: Dict[tuple, tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL_SEC = 180.0


def _get_amadeus_config() -> tuple[str, str, str]:
    cid = (os.environ.get("AMADEUS_CLIENT_ID") or "").strip()
    csec = (os.environ.get("AMADEUS_CLIENT_SECRET") or "").strip()
    env_mode = (os.environ.get("AMADEUS_ENV") or "").strip().lower()
    if not (cid and csec):
        try:
            s = get_settings()
            cid = cid or (s.get("amadeus_client_id") or "").strip()
            csec = csec or (s.get("amadeus_client_secret") or "").strip()
            if not env_mode:
                env_mode = (s.get("amadeus_env") or "test").strip().lower()
        except Exception:
            pass
    base_url = "https://api.amadeus.com" if env_mode in ("prod", "production") else "https://test.api.amadeus.com"
    return cid, csec, base_url


def is_amadeus_configured() -> bool:
    cid, csec, _ = _get_amadeus_config()
    return bool(cid and csec)


def _parse_iso_duration(dur_str: str) -> int:
    m = _DUR_RE.search(dur_str or "")
    if not m:
        return 0
    hrs = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    return hrs * 3600 + mins * 60


async def _get_access_token(client: httpx.AsyncClient, cid: str, csec: str, base_url: str) -> str:
    now = time.monotonic()
    if _TOKEN_CACHE["token"] and _TOKEN_CACHE["base"] == base_url and now < _TOKEN_CACHE["exp"]:
        return _TOKEN_CACHE["token"]
    resp = await client.post(
        f"{base_url}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": csec,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=8.0,
    )
    if resp.status_code != 200:
        return ""
    body = resp.json()
    tok = body.get("access_token") or ""
    expires_in = int(body.get("expires_in") or 1799)
    if tok:
        _TOKEN_CACHE["token"] = tok
        _TOKEN_CACHE["base"] = base_url
        _TOKEN_CACHE["exp"] = now + max(60, expires_in - 60)
    return tok


def _parse_itinerary_metrics(itin: Dict[str, Any], carriers_dict: Dict[str, str]) -> Dict[str, Any]:
    segs = itin.get("segments") or []
    dur_sec = _parse_iso_duration(itin.get("duration") or "")
    layovers: List[Dict[str, Any]] = []
    codes: List[str] = []
    names: List[str] = []
    for i, s in enumerate(segs):
        cc = (s.get("carrierCode") or "").upper()
        if cc and cc not in codes:
            codes.append(cc)
            cname = carriers_dict.get(cc) or cc
            names.append(cname.title() if cname.isupper() and len(cname) > 3 else cname)
        if i < len(segs) - 1:
            arr_t = (s.get("arrival") or {}).get("at") or ""
            nxt_t = (segs[i + 1].get("departure") or {}).get("at") or ""
            lay_sec = 0
            try:
                t1 = datetime.fromisoformat(arr_t[:19])
                t2 = datetime.fromisoformat(nxt_t[:19])
                lay_sec = max(0, int((t2 - t1).total_seconds()))
            except Exception:
                pass
            stop_code = ((s.get("arrival") or {}).get("iataCode") or "").upper()
            stop_meta = get_airport_meta(stop_code)
            layovers.append({
                "airport_code": stop_code,
                "city": stop_meta["city"],
                "duration_sec": lay_sec,
                "duration_str": format_duration_hm(lay_sec),
            })
    return {
        "segs": segs,
        "duration_sec": dur_sec,
        "stops_count": max(0, len(segs) - 1),
        "codes": codes,
        "names": names,
        "layovers": layovers,
    }


async def _fetch_amadeus_pair(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    origin: str,
    dest: str,
    dep_date: str,
    ret_date: Optional[str],
    max_stops: int,
    max_price: Optional[int],
    max_travel_sec: int,
    max_layover_sec: int,
) -> List[Dict[str, Any]]:
    cache_k = (base_url, origin, dest, dep_date, ret_date or "", max_stops)
    now = time.monotonic()
    cached = _SEARCH_CACHE.get(cache_k)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return [dict(x) for x in cached[1]]

    params: Dict[str, Any] = {
        "originLocationCode": origin,
        "destinationLocationCode": dest,
        "departureDate": dep_date,
        "adults": 1,
        "currencyCode": "PLN",
        "max": 12,
    }
    if ret_date:
        params["returnDate"] = ret_date
    if max_stops == 0:
        params["nonStop"] = "true"
    if max_price:
        params["maxPrice"] = int(max_price)

    try:
        resp = await client.get(
            f"{base_url}/v2/shopping/flight-offers",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
    except Exception:
        return []

    carriers_dict = ((payload.get("dictionaries") or {}).get("carriers")) or {}
    offers = payload.get("data") or []
    results: List[Dict[str, Any]] = []

    for off in offers:
        try:
            price_obj = off.get("price") or {}
            if (price_obj.get("currency") or "PLN").upper() != "PLN":
                continue
            price_val = round(float(price_obj.get("grandTotal") or price_obj.get("total") or 0), 2)
            if price_val <= 0 or (max_price and price_val > max_price):
                continue

            itins = off.get("itineraries") or []
            if not itins:
                continue
            out_m = _parse_itinerary_metrics(itins[0], carriers_dict)
            if not out_m["segs"] or out_m["stops_count"] > max_stops:
                continue
            if out_m["duration_sec"] > max_travel_sec:
                continue

            in_m = _parse_itinerary_metrics(itins[1], carriers_dict) if len(itins) > 1 else None
            if ret_date and (not in_m or not in_m["segs"] or in_m["stops_count"] > max_stops or in_m["duration_sec"] > max_travel_sec):
                continue

            all_lays = out_m["layovers"] + (in_m["layovers"] if in_m else [])
            max_lay = max((l["duration_sec"] for l in all_lays), default=0)
            if max_lay > max_layover_sec:
                continue

            out_dep = (out_m["segs"][0].get("departure") or {}).get("at") or ""
            out_arr = (out_m["segs"][-1].get("arrival") or {}).get("at") or ""
            in_dep = (in_m["segs"][0].get("departure") or {}).get("at") or None if in_m else None
            in_arr = (in_m["segs"][-1].get("arrival") or {}).get("at") or None if in_m else None
            nights = max(0, (datetime.fromisoformat(in_dep[:10]) - datetime.fromisoformat(out_dep[:10])).days) if in_dep else 0

            orig_meta = get_airport_meta(origin)
            dest_meta = get_airport_meta(dest)
            all_codes = list(dict.fromkeys(out_m["codes"] + (in_m["codes"] if in_m else [])))
            all_names = list(dict.fromkeys(out_m["names"] + (in_m["names"] if in_m else [])))
            carrier_disp = " + ".join(all_names[:2]) if all_names else "Amadeus GDS"

            links = build_booking_links(origin, dest, out_dep, in_dep, 1)
            dist = estimate_distance_km(origin, dest)
            total_stops = max(out_m["stops_count"], in_m["stops_count"] if in_m else 0)
            lay_str = (
                "Lot bezpośredni (bez przesiadek)"
                if (out_m["stops_count"] + (in_m["stops_count"] if in_m else 0)) == 0
                else " • ".join(f"{l['city']} ({l['airport_code']}) – {l['duration_str']}" for l in all_lays[:3])
            )

            results.append({
                "id": f"AM-{origin}-{dest}-{out_dep[:10]}-{(in_dep or 'OW')[:10]}-{'_'.join(all_codes)}-{total_stops}",
                "trip_type": "roundtrip" if ret_date else "oneway",
                "source_engine": "amadeus_gds",
                "source_label": f"Amadeus GDS ({carrier_disp})",
                "carrier_code": all_codes[0] if all_codes else "YY",
                "carrier_name": carrier_disp,
                "return_carrier_code": in_m["codes"][0] if (in_m and in_m["codes"]) else (all_codes[0] if all_codes else None),
                "return_carrier_name": " + ".join(in_m["names"][:2]) if (in_m and in_m["names"]) else carrier_disp,
                "is_mixed_carrier": len(all_codes) > 1,
                "origin_iata": origin,
                "origin_city": orig_meta["city"],
                "origin_name": orig_meta["name"],
                "dest_iata": dest,
                "dest_city": dest_meta["city"],
                "dest_name": dest_meta["name"],
                "dest_country": dest_meta["country"],
                "dest_country_code": dest_meta["country_code"],
                "flag": country_code_to_flag(dest_meta["country_code"]),
                "outbound_departure": out_dep,
                "outbound_arrival": out_arr,
                "inbound_departure": in_dep,
                "inbound_arrival": in_arr,
                "nights": nights,
                "stops_count": total_stops,
                "outbound_stops": out_m["stops_count"],
                "inbound_stops": in_m["stops_count"] if in_m else 0,
                "outbound_duration_sec": out_m["duration_sec"],
                "inbound_duration_sec": in_m["duration_sec"] if in_m else 0,
                "outbound_duration_str": format_duration_hm(out_m["duration_sec"]),
                "inbound_duration_str": format_duration_hm(in_m["duration_sec"]) if in_m else None,
                "max_layover_sec": max_lay,
                "max_layover_str": format_duration_hm(max_lay) if max_lay > 0 else "Brak",
                "total_layover_sec": sum(l["duration_sec"] for l in all_lays),
                "layover_details": all_lays,
                "layover_summary": lay_str,
                "price_pln": price_val,
                "outbound_price_pln": price_val if not ret_date else None,
                "inbound_price_pln": None,
                "distance_km": dist,
                "flight_number": " / ".join(all_codes),
                "booking_links": links,
                "primary_booking_url": links["google_flights"],
            })
        except Exception:
            continue

    if results:
        if len(_SEARCH_CACHE) >= 80:
            _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)), None)
        _SEARCH_CACHE[cache_k] = (now, [dict(x) for x in results])
    return results


async def search_amadeus_flights(
    airports: List[str],
    date_from: str,
    date_to: str,
    trip_type: str = "roundtrip",
    nights_min: int = 2,
    nights_max: int = 7,
    max_price: Optional[int] = None,
    dest_iata_list: Optional[List[str]] = None,
    max_stops: int = 1,
    max_layover_hours: int = 6,
    max_travel_hours: int = 20,
) -> List[Dict[str, Any]]:
    cid, csec, base_url = _get_amadeus_config()
    if not (cid and csec) or not dest_iata_list:
        return []

    try:
        dep_dt = datetime.fromisoformat(date_from[:10]).date() + timedelta(days=2)
        end_dt = datetime.fromisoformat(date_to[:10]).date()
        if dep_dt > end_dt:
            dep_dt = datetime.fromisoformat(date_from[:10]).date()
    except Exception:
        return []

    ret_str: Optional[str] = None
    if trip_type == "roundtrip":
        mid_nights = max(1, (nights_min + nights_max) // 2)
        ret_dt = min(end_dt, dep_dt + timedelta(days=mid_nights))
        if ret_dt <= dep_dt:
            ret_dt = dep_dt + timedelta(days=max(1, nights_min))
        ret_str = ret_dt.isoformat()

    # Query up to 4 originxtarget pairs per scan to respect free-tier request quotas cleanly
    src_slice = [a.strip().upper() for a in airports if a.strip()][:2]
    dst_limit = 4 if len(src_slice) <= 1 else 2
    dst_slice = [d.strip().upper() for d in dest_iata_list if d.strip()][:dst_limit]
    pairs = [(o, d) for o in src_slice for d in dst_slice if o != d][:4]
    if not pairs:
        return []

    async with httpx.AsyncClient() as client:
        token = await _get_access_token(client, cid, csec, base_url)
        if not token:
            return []
        batches = await asyncio.gather(
            *[
                _fetch_amadeus_pair(
                    client=client,
                    token=token,
                    base_url=base_url,
                    origin=o,
                    dest=d,
                    dep_date=dep_dt.isoformat(),
                    ret_date=ret_str,
                    max_stops=max_stops,
                    max_price=max_price,
                    max_travel_sec=int(max_travel_hours) * 3600,
                    max_layover_sec=int(max_layover_hours) * 3600,
                )
                for o, d in pairs
            ],
            return_exceptions=True,
        )

    merged: List[Dict[str, Any]] = []
    for b in batches:
        if isinstance(b, list):
            merged.extend(b)
    return merged
