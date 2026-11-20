import asyncio
import os
import time
from datetime import datetime
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

TP_API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
_TP_CACHE: Dict[tuple, tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL_SEC = 180.0

_CARRIER_NAMES = {
    "FR": "Ryanair", "W6": "Wizz Air", "W4": "Wizz Air Malta", "LO": "LOT Polish Airlines",
    "LH": "Lufthansa", "DY": "Norwegian", "D8": "Norwegian", "U2": "easyJet",
    "VY": "Vueling", "HV": "Transavia", "BT": "airBaltic", "KL": "KLM",
    "AF": "Air France", "TP": "TAP Air Portugal", "LX": "SWISS", "OS": "Austrian Airlines",
    "SK": "SAS", "AY": "Finnair", "BA": "British Airways", "EK": "Emirates",
    "QR": "Qatar Airways", "TK": "Turkish Airlines", "LS": "Jet2", "ENT": "Enter Air",
}


def _get_tp_config() -> tuple[str, str]:
    tok = (os.environ.get("TRAVELPAYOUTS_TOKEN") or "").strip()
    marker = (os.environ.get("TRAVELPAYOUTS_MARKER") or "").strip()
    if not tok:
        try:
            s = get_settings()
            tok = (s.get("travelpayouts_token") or "").strip()
            marker = marker or (s.get("travelpayouts_marker") or "").strip()
        except Exception:
            pass
    return tok, marker


def is_travelpayouts_configured() -> bool:
    tok, _ = _get_tp_config()
    return bool(tok)


async def _fetch_tp_origin(
    client: httpx.AsyncClient,
    token: str,
    marker: str,
    origin: str,
    destination: Optional[str],
    date_from: str,
    date_to: str,
    trip_type: str,
    nights_min: int,
    nights_max: int,
    max_stops: int,
    max_price: Optional[int],
) -> List[Dict[str, Any]]:
    one_way = trip_type == "oneway"
    cache_k = (origin, destination or "ANY", date_from[:7], one_way, max_stops)
    now = time.monotonic()
    cached = _TP_CACHE.get(cache_k)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        items_res = [dict(x) for x in cached[1]]
        return [x for x in items_res if not max_price or x["price_pln"] <= max_price]

    params: Dict[str, Any] = {
        "origin": origin,
        "currency": "pln",
        "one_way": "true" if one_way else "false",
        "sorting": "price",
        "limit": 30,
        "show_to_affiliates": "true",
        "departure_at": date_from[:7],
    }
    if destination:
        params["destination"] = destination
    if max_stops == 0:
        params["direct"] = "true"

    try:
        resp = await client.get(
            TP_API_URL,
            params=params,
            headers={"X-Access-Token": token},
            timeout=9.0,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("success"):
            return []
        rows = data.get("data") or []
    except Exception:
        return []

    results: List[Dict[str, Any]] = []
    for row in rows:
        try:
            orig_code = (row.get("origin") or origin).upper()
            dest_code = (row.get("destination") or "").upper()
            if not orig_code or not dest_code or orig_code == dest_code:
                continue

            stops = int(row.get("number_of_changes") or 0)
            if stops > max_stops:
                continue

            price_val = round(float(row.get("value") or 0), 2)
            if price_val <= 0:
                continue

            dep_str = (row.get("departure_at") or row.get("depart_date") or "")[:19]
            ret_str = (row.get("return_at") or row.get("return_date") or "")[:19] if not one_way else None
            if not dep_str or dep_str[:10] < date_from[:10] or dep_str[:10] > date_to[:10]:
                continue
            if not one_way:
                if not ret_str or ret_str[:10] > date_to[:10]:
                    continue
                nights = (datetime.fromisoformat(ret_str[:10]) - datetime.fromisoformat(dep_str[:10])).days
                if nights < nights_min or nights > nights_max:
                    continue
            else:
                nights = 0

            orig_meta = get_airport_meta(orig_code)
            dest_meta = get_airport_meta(dest_code)
            airline_code = (row.get("airline") or "TP").upper()
            airline_name = _CARRIER_NAMES.get(airline_code, airline_code)
            dist = int(row.get("distance") or estimate_distance_km(orig_code, dest_code) or 1000)

            def _to_sec(val: Any, fallback_stops: int) -> int:
                m = int(val or 0)
                if m > 0:
                    return m * 60 if m < 2500 else m
                base = int(1800 + (dist / 730.0) * 3600)
                return base + fallback_stops * 5400

            est_out_dur = _to_sec(row.get("duration_to") or row.get("duration"), stops)
            est_in_dur = _to_sec(row.get("duration_back") or row.get("duration_to") or row.get("duration"), stops) if not one_way else 0

            links = build_booking_links(orig_code, dest_code, dep_str, ret_str, 1)
            raw_link = row.get("link") or ""
            booking_url = links["kiwi"]
            if raw_link.startswith("/"):
                sep = "&" if "?" in raw_link else "?"
                marker_qs = f"{sep}marker={marker}" if marker else ""
                booking_url = f"https://www.aviasales.com{raw_link}{marker_qs}"

            results.append({
                "id": f"TP-{orig_code}-{dest_code}-{dep_str[:10]}-{(ret_str or 'OW')[:10]}-{airline_code}-{stops}",
                "trip_type": "roundtrip" if not one_way else "oneway",
                "source_engine": "travelpayouts",
                "source_label": f"Travelpayouts ({airline_name})",
                "carrier_code": airline_code,
                "carrier_name": airline_name,
                "return_carrier_code": airline_code,
                "return_carrier_name": airline_name,
                "is_mixed_carrier": False,
                "origin_iata": orig_code,
                "origin_city": orig_meta["city"],
                "origin_name": orig_meta["name"],
                "dest_iata": dest_code,
                "dest_city": dest_meta["city"],
                "dest_name": dest_meta["name"],
                "dest_country": dest_meta["country"],
                "dest_country_code": dest_meta["country_code"],
                "flag": country_code_to_flag(dest_meta["country_code"]),
                "outbound_departure": dep_str if "T" in dep_str else f"{dep_str[:10]}T10:00:00",
                "outbound_arrival": dep_str if "T" in dep_str else f"{dep_str[:10]}T12:30:00",
                "inbound_departure": (ret_str if "T" in ret_str else f"{ret_str[:10]}T15:00:00") if ret_str else None,
                "inbound_arrival": (ret_str if "T" in ret_str else f"{ret_str[:10]}T17:30:00") if ret_str else None,
                "nights": nights,
                "stops_count": stops,
                "outbound_stops": stops,
                "inbound_stops": stops if not one_way else 0,
                "outbound_duration_sec": est_out_dur,
                "inbound_duration_sec": est_in_dur,
                "outbound_duration_str": format_duration_hm(est_out_dur),
                "inbound_duration_str": format_duration_hm(est_in_dur) if not one_way else None,
                "max_layover_sec": 3600 * 2 if stops > 0 else 0,
                "max_layover_str": "Przesiadka" if stops > 0 else "Brak",
                "total_layover_sec": 3600 * 2 * stops,
                "layover_details": [],
                "layover_summary": "Lot bezpośredni (bez przesiadek)" if stops == 0 else f"Przesiadki: {stops}",
                "price_pln": price_val,
                "outbound_price_pln": price_val if one_way else None,
                "inbound_price_pln": None,
                "distance_km": dist,
                "flight_number": str(row.get("flight_number") or airline_code),
                "booking_links": links,
                "primary_booking_url": booking_url,
            })
        except Exception:
            continue

    if results:
        if len(_TP_CACHE) >= 80:
            _TP_CACHE.pop(next(iter(_TP_CACHE)), None)
        _TP_CACHE[cache_k] = (now, [dict(x) for x in results])

    return [x for x in results if not max_price or x["price_pln"] <= max_price]


async def search_travelpayouts_flights(
    airports: List[str],
    date_from: str,
    date_to: str,
    trip_type: str = "roundtrip",
    nights_min: int = 2,
    nights_max: int = 12,
    max_price: Optional[int] = None,
    dest_iata_list: Optional[List[str]] = None,
    max_stops: int = 2,
) -> List[Dict[str, Any]]:
    token, marker = _get_tp_config()
    if not token:
        return []

    src_slice = [a.strip().upper() for a in airports if a.strip()][:4]
    dst_target = dest_iata_list[0].strip().upper() if dest_iata_list and len(dest_iata_list) == 1 else None

    async with httpx.AsyncClient() as client:
        batches = await asyncio.gather(
            *[
                _fetch_tp_origin(
                    client=client,
                    token=token,
                    marker=marker,
                    origin=o,
                    destination=dst_target,
                    date_from=date_from,
                    date_to=date_to,
                    trip_type=trip_type,
                    nights_min=nights_min,
                    nights_max=nights_max,
                    max_stops=max_stops,
                    max_price=max_price,
                )
                for o in src_slice
            ],
            return_exceptions=True,
        )

    merged: List[Dict[str, Any]] = []
    dst_filter = {d.upper() for d in (dest_iata_list or [])}
    for b in batches:
        if isinstance(b, list):
            for f in b:
                if dst_filter and f["dest_iata"] not in dst_filter:
                    continue
                merged.append(f)
    return merged
