import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
import httpx

from backend.airports import (
    country_code_to_flag,
    estimate_distance_km,
    build_booking_links,
    format_duration_hm,
)

RYANAIR_BASE = "https://www.ryanair.com/api/farfnd/v4"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _estimate_direct_duration_sec(dep_dt: str, arr_dt: str, dist_km: Optional[int]) -> int:
    if dist_km and dist_km > 0:
        # Realistic flight block time: 30 min taxi/climb + distance at ~740 km/h
        return int(1800 + (dist_km / 740.0) * 3600)
    try:
        t1 = datetime.fromisoformat(dep_dt[:19])
        t2 = datetime.fromisoformat(arr_dt[:19])
        diff = int((t2 - t1).total_seconds())
        if 1800 <= diff <= 25200:
            return diff
    except Exception:
        pass
    return 7200


async def _fetch_airport_one_way(
    client: httpx.AsyncClient,
    origin_iata: str,
    date_from: str,
    date_to: str,
    max_price: Optional[int] = None,
    dest_iata: Optional[str] = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "departureAirportIataCode": origin_iata,
        "market": "pl-pl",
        "adultPaxCount": 1,
        "outboundDepartureDateFrom": date_from,
        "outboundDepartureDateTo": date_to,
        "currency": "PLN",
    }
    if max_price:
        params["priceValueTo"] = max_price
    if dest_iata:
        params["arrivalAirportIataCode"] = dest_iata.upper()

    try:
        resp = await client.get(f"{RYANAIR_BASE}/oneWayFares", params=params, timeout=10.0)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []

    results: List[Dict[str, Any]] = []
    for item in data.get("fares", []):
        out = item.get("outbound") or {}
        dep = out.get("departureAirport") or {}
        arr = out.get("arrivalAirport") or {}
        price_obj = out.get("price") or {}
        price_val = price_obj.get("value")
        if price_val is None:
            continue

        orig_code = dep.get("iataCode", origin_iata)
        dest_code = arr.get("iataCode", "")
        country_name = arr.get("countryName", "")
        dep_dt = out.get("departureDate", "")
        arr_dt = out.get("arrivalDate", "")

        dist = estimate_distance_km(orig_code, dest_code)
        out_dur_sec = _estimate_direct_duration_sec(dep_dt, arr_dt, dist)
        links = build_booking_links(orig_code, dest_code, dep_dt)

        results.append({
            "id": f"FR-OW-{orig_code}-{dest_code}-{dep_dt[:10]}",
            "trip_type": "oneway",
            "source_engine": "ryanair_direct",
            "source_label": "Ryanair Direct API",
            "carrier_code": "FR",
            "carrier_name": "Ryanair",
            "return_carrier_code": None,
            "return_carrier_name": None,
            "is_mixed_carrier": False,
            "origin_iata": orig_code,
            "origin_city": dep.get("city", {}).get("name") or dep.get("name", orig_code),
            "origin_name": dep.get("name", orig_code),
            "dest_iata": dest_code,
            "dest_city": arr.get("city", {}).get("name") or arr.get("name", dest_code),
            "dest_name": arr.get("name", dest_code),
            "dest_country": country_name,
            "dest_country_code": (arr.get("city", {}).get("countryCode") or "").upper(),
            "flag": country_code_to_flag(arr.get("city", {}).get("countryCode")),
            "outbound_departure": dep_dt,
            "outbound_arrival": arr_dt,
            "inbound_departure": None,
            "inbound_arrival": None,
            "nights": 0,
            "stops_count": 0,
            "outbound_stops": 0,
            "inbound_stops": 0,
            "outbound_duration_sec": out_dur_sec,
            "inbound_duration_sec": 0,
            "outbound_duration_str": format_duration_hm(out_dur_sec),
            "inbound_duration_str": None,
            "max_layover_sec": 0,
            "max_layover_str": "Brak",
            "total_layover_sec": 0,
            "layover_details": [],
            "layover_summary": "Lot bezpośredni (bez przesiadek)",
            "price_pln": round(float(price_val), 2),
            "outbound_price_pln": round(float(price_val), 2),
            "inbound_price_pln": None,
            "distance_km": dist,
            "flight_number": out.get("flightNumber", ""),
            "booking_links": links,
            "primary_booking_url": links["ryanair"],
        })
    return results


async def _fetch_airport_round_trip(
    client: httpx.AsyncClient,
    origin_iata: str,
    date_from: str,
    date_to: str,
    nights_min: int = 2,
    nights_max: int = 5,
    max_price: Optional[int] = None,
    dest_iata: Optional[str] = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "departureAirportIataCode": origin_iata,
        "market": "pl-pl",
        "adultPaxCount": 1,
        "outboundDepartureDateFrom": date_from,
        "outboundDepartureDateTo": date_to,
        "inboundDepartureDateFrom": date_from,
        "inboundDepartureDateTo": date_to,
        "durationFrom": nights_min,
        "durationTo": nights_max,
        "currency": "PLN",
    }
    if max_price:
        params["priceValueTo"] = max_price
    if dest_iata:
        params["arrivalAirportIataCode"] = dest_iata.upper()

    try:
        resp = await client.get(f"{RYANAIR_BASE}/roundTripFares", params=params, timeout=12.0)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []

    results: List[Dict[str, Any]] = []
    for item in data.get("fares", []):
        out = item.get("outbound") or {}
        inb = item.get("inbound") or {}
        summary = item.get("summary") or {}
        total_price_obj = summary.get("price") or {}
        total_price = total_price_obj.get("value")
        if total_price is None:
            continue

        dep = out.get("departureAirport") or {}
        arr = out.get("arrivalAirport") or {}
        in_dep = inb.get("departureAirport") or {}
        in_arr = inb.get("arrivalAirport") or {}
        orig_code = (dep.get("iataCode") or origin_iata).upper()
        dest_code = (arr.get("iataCode") or "").upper()
        in_orig_code = (in_dep.get("iataCode") or "").upper()
        in_dest_code = (in_arr.get("iataCode") or "").upper()
        if not dest_code or (in_orig_code and in_orig_code != dest_code) or (in_dest_code and in_dest_code != orig_code):
            continue
        dep_dt = out.get("departureDate", "")
        arr_dt = out.get("arrivalDate", "")
        in_dep_dt = inb.get("departureDate", "")
        in_arr_dt = inb.get("arrivalDate", "")

        nights = summary.get("tripDurationDays")
        if nights is None and dep_dt and in_dep_dt:
            try:
                d1 = datetime.fromisoformat(dep_dt[:10])
                d2 = datetime.fromisoformat(in_dep_dt[:10])
                nights = max(0, (d2 - d1).days)
            except Exception:
                nights = nights_min

        dist = estimate_distance_km(orig_code, dest_code)
        out_dur_sec = _estimate_direct_duration_sec(dep_dt, arr_dt, dist)
        in_dur_sec = _estimate_direct_duration_sec(in_dep_dt, in_arr_dt, dist)

        links = build_booking_links(orig_code, dest_code, dep_dt, in_dep_dt)
        country_code = (arr.get("city", {}).get("countryCode") or "").upper()

        out_price = (out.get("price") or {}).get("value")
        in_price = (inb.get("price") or {}).get("value")

        results.append({
            "id": f"FR-RT-{orig_code}-{dest_code}-{dep_dt[:10]}-{in_dep_dt[:10]}",
            "trip_type": "roundtrip",
            "source_engine": "ryanair_direct",
            "source_label": "Ryanair Direct API",
            "carrier_code": "FR",
            "carrier_name": "Ryanair",
            "return_carrier_code": "FR",
            "return_carrier_name": "Ryanair",
            "is_mixed_carrier": False,
            "origin_iata": orig_code,
            "origin_city": dep.get("city", {}).get("name") or dep.get("name", orig_code),
            "origin_name": dep.get("name", orig_code),
            "dest_iata": dest_code,
            "dest_city": arr.get("city", {}).get("name") or arr.get("name", dest_code),
            "dest_name": arr.get("name", dest_code),
            "dest_country": arr.get("countryName", ""),
            "dest_country_code": country_code,
            "flag": country_code_to_flag(country_code),
            "outbound_departure": dep_dt,
            "outbound_arrival": arr_dt,
            "inbound_departure": in_dep_dt,
            "inbound_arrival": in_arr_dt,
            "nights": nights or 0,
            "stops_count": 0,
            "outbound_stops": 0,
            "inbound_stops": 0,
            "outbound_duration_sec": out_dur_sec,
            "inbound_duration_sec": in_dur_sec,
            "outbound_duration_str": format_duration_hm(out_dur_sec),
            "inbound_duration_str": format_duration_hm(in_dur_sec),
            "max_layover_sec": 0,
            "max_layover_str": "Brak",
            "total_layover_sec": 0,
            "layover_details": [],
            "layover_summary": "Lot bezpośredni (bez przesiadek)",
            "price_pln": round(float(total_price), 2),
            "outbound_price_pln": round(float(out_price), 2) if out_price is not None else None,
            "inbound_price_pln": round(float(in_price), 2) if in_price is not None else None,
            "distance_km": dist,
            "flight_number": f"{out.get('flightNumber', '')} / {inb.get('flightNumber', '')}",
            "booking_links": links,
            "primary_booking_url": links["ryanair"],
        })
    return results


async def search_ryanair_flights(
    airports: List[str],
    date_from: str,
    date_to: str,
    trip_type: str = "roundtrip",
    nights_min: int = 2,
    nights_max: int = 5,
    max_price: Optional[int] = None,
    dest_iata: Optional[str] = None,
) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(headers=HEADERS) as client:
        if trip_type == "oneway":
            tasks = [
                _fetch_airport_one_way(client, code.strip().upper(), date_from, date_to, max_price, dest_iata)
                for code in airports if code.strip()
            ]
        else:
            tasks = [
                _fetch_airport_round_trip(
                    client, code.strip().upper(), date_from, date_to, nights_min, nights_max, max_price, dest_iata
                )
                for code in airports if code.strip()
            ]
        grouped = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: List[Dict[str, Any]] = []
    for item in grouped:
        if isinstance(item, list):
            all_results.extend(item)
    return all_results


async def get_ryanair_monthly_calendar(
    origin_iata: str,
    dest_iata: str,
    month_date: str,
) -> Dict[str, Any]:
    origin_iata = origin_iata.upper().strip()
    dest_iata = dest_iata.upper().strip()
    if len(month_date) == 7:
        month_date = f"{month_date}-01"

    url_out = f"{RYANAIR_BASE}/oneWayFares/{origin_iata}/{dest_iata}/cheapestPerDay"
    url_in = f"{RYANAIR_BASE}/oneWayFares/{dest_iata}/{origin_iata}/cheapestPerDay"
    params = {"outboundMonthOfDate": month_date, "currency": "PLN"}

    async with httpx.AsyncClient(headers=HEADERS) as client:
        res_out, res_in = await asyncio.gather(
            client.get(url_out, params=params, timeout=10.0),
            client.get(url_in, params=params, timeout=10.0),
            return_exceptions=True,
        )

    outbound_days = []
    inbound_days = []

    if not isinstance(res_out, Exception) and res_out.status_code == 200:
        for d in res_out.json().get("outbound", {}).get("fares", []):
            if not d.get("unavailable") and not d.get("soldOut") and d.get("price"):
                outbound_days.append({
                    "day": d.get("day"),
                    "departure": d.get("departureDate"),
                    "arrival": d.get("arrivalDate"),
                    "price_pln": round(float(d["price"]["value"]), 2),
                })

    if not isinstance(res_in, Exception) and res_in.status_code == 200:
        for d in res_in.json().get("outbound", {}).get("fares", []):
            if not d.get("unavailable") and not d.get("soldOut") and d.get("price"):
                inbound_days.append({
                    "day": d.get("day"),
                    "departure": d.get("departureDate"),
                    "arrival": d.get("arrivalDate"),
                    "price_pln": round(float(d["price"]["value"]), 2),
                })

    best_combos = []
    in_by_day = {d["day"]: d for d in inbound_days}
    for out_d in outbound_days:
        try:
            dt1 = datetime.fromisoformat(out_d["day"])
        except Exception:
            continue
        for in_day_str, in_d in in_by_day.items():
            try:
                dt2 = datetime.fromisoformat(in_day_str)
            except Exception:
                continue
            nights = (dt2 - dt1).days
            if 1 <= nights <= 10:
                total = round(out_d["price_pln"] + in_d["price_pln"], 2)
                links = build_booking_links(origin_iata, dest_iata, out_d["day"], in_day_str)
                best_combos.append({
                    "outbound_day": out_d["day"],
                    "inbound_day": in_day_str,
                    "nights": nights,
                    "outbound_price_pln": out_d["price_pln"],
                    "inbound_price_pln": in_d["price_pln"],
                    "total_price_pln": total,
                    "booking_url": links["ryanair"],
                })

    best_combos.sort(key=lambda x: x["total_price_pln"])

    return {
        "origin_iata": origin_iata,
        "dest_iata": dest_iata,
        "month": month_date[:7],
        "outbound_days": outbound_days,
        "inbound_days": inbound_days,
        "best_combinations": best_combos[:15],
    }
