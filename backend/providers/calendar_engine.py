import asyncio
import calendar
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
import httpx

from backend.airports import (
    POLISH_AIRPORTS,
    WORLD_PRESETS,
    resolve_destination_iata_list,
    build_booking_links,
)
from backend.providers.ryanair import get_ryanair_monthly_calendar
from backend.providers.kiwi_multi import (
    search_kiwi_multi_airline,
    build_disjoint_route_pairs,
    _get_kiwi_sem,
)

KIWI_GRAPHQL_URL = "https://api.skypicker.com/umbrella/v2/graphql"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}
_CAL_CACHE: Dict[tuple, tuple[float, Dict[str, Any]]] = {}
_CAL_CACHE_TTL = 150.0

CAL_ONEWAY_QUERY = """
query SearchOneWayItinerariesQuery($search: SearchOnewayInput, $filter: ItinerariesFilterInput, $options: ItinerariesOptionsInput) {
  onewayItineraries(search: $search, filter: $filter, options: $options) {
    __typename
    ... on Itineraries {
      itineraries {
        id
        price { amount }
        bookingOptions {
          edges {
            node {
              bookingUrl
            }
          }
        }
        ... on ItineraryOneWay {
          sector {
            duration
            sectorSegments {
              segment {
                source { station { code city { name } } localTime }
                destination { station { code city { name } } localTime }
                carrier { name code }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _month_bounds(month_str: str) -> tuple[str, str, str]:
    clean = (month_str or "")[:7]
    try:
        y, m = int(clean[:4]), int(clean[5:7])
    except Exception:
        now = datetime.now()
        y, m = now.year, now.month
    last_day = calendar.monthrange(y, m)[1]
    start_day = f"{y:04d}-{m:02d}-01"
    end_day = f"{y:04d}-{m:02d}-{last_day:02d}"
    return f"{y:04d}-{m:02d}", start_day, end_day


async def _fetch_kiwi_month_single(
    source_codes: List[str],
    dest_codes: List[str],
    start_day: str,
    end_day: str,
    max_stops: int = 1,
) -> tuple[Dict[str, Dict[str, Any]], Dict[tuple[str, str, str], Dict[str, Any]]]:
    if not source_codes or not dest_codes:
        return {}, {}
    payload = {
        "query": CAL_ONEWAY_QUERY,
        "variables": {
            "search": {
                "itinerary": {
                    "source": {"ids": [f"Station:airport:{c}" for c in source_codes]},
                    "destination": {"ids": [f"Station:airport:{c}" for c in dest_codes]},
                    "outboundDepartureDate": {
                        "start": f"{start_day}T00:00:00",
                        "end": f"{end_day}T23:59:59",
                    },
                },
                "passengers": {"adults": 1, "children": 0, "infants": 0},
            },
            "filter": {"limit": 120, "maxStopsCount": max_stops},
            "options": {"currency": "PLN", "locale": "pl", "sortBy": "PRICE", "partner": "skypicker"},
        },
    }

    sem = _get_kiwi_sem()
    async with sem:
        async with httpx.AsyncClient(headers=HEADERS) as client:
            try:
                resp = await client.post(
                    f"{KIWI_GRAPHQL_URL}?featureName=SearchOneWayItinerariesQuery",
                    json=payload,
                    timeout=14.0,
                )
                if resp.status_code != 200:
                    return {}, {}
                data = resp.json()
            except Exception:
                return {}, {}

    by_day: Dict[str, Dict[str, Any]] = {}
    by_route_day: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    itins = (data.get("data") or {}).get("onewayItineraries", {}).get("itineraries", [])
    for it in itins:
        try:
            price_val = round(float(it["price"]["amount"]), 2)
            segs = it["sector"]["sectorSegments"]

            # Skip hidden cross-airport transfer during layover
            has_station_change = False
            for idx in range(len(segs) - 1):
                arr_c = (segs[idx]["segment"]["destination"]["station"].get("code") or "").upper()
                nxt_c = (segs[idx + 1]["segment"]["source"]["station"].get("code") or "").upper()
                if arr_c and nxt_c and arr_c != nxt_c:
                    has_station_change = True
                    break
            if has_station_change:
                continue

            first_seg = segs[0]["segment"]
            last_seg = segs[-1]["segment"]

            dep_dt = first_seg["source"]["localTime"]
            day = dep_dt[:10]
            orig_code = first_seg["source"]["station"]["code"].upper()
            dest_code = last_seg["destination"]["station"]["code"].upper()
            if not orig_code or not dest_code or orig_code == dest_code:
                continue
            orig_city = (first_seg["source"]["station"].get("city") or {}).get("name", orig_code)
            dest_city = (last_seg["destination"]["station"].get("city") or {}).get("name", dest_code)
            carrier_name = first_seg["carrier"]["name"]
            stops_cnt = max(0, len(segs) - 1)

            edges = (it.get("bookingOptions") or {}).get("edges") or []
            b_path = edges[0]["node"].get("bookingUrl") if edges else None
            links = build_booking_links(orig_code, dest_code, dep_dt, None, 1, b_path)
            booking_url = links["kiwi"]
            if stops_cnt == 0 and first_seg["carrier"]["code"] == "FR":
                booking_url = links["ryanair"]

            entry = {
                "day": day,
                "departure": dep_dt,
                "arrival": last_seg["destination"]["localTime"],
                "origin_iata": orig_code,
                "origin_city": orig_city,
                "dest_iata": dest_code,
                "dest_city": dest_city,
                "carrier_name": carrier_name,
                "stops_count": stops_cnt,
                "price_pln": price_val,
                "booking_url": booking_url,
            }

            if day not in by_day or price_val < by_day[day]["price_pln"]:
                by_day[day] = entry

            r_key = (day, orig_code, dest_code)
            if r_key not in by_route_day or price_val < by_route_day[r_key]["price_pln"]:
                by_route_day[r_key] = entry
        except Exception:
            continue
    return by_day, by_route_day


async def _fetch_kiwi_month_direction(
    source_codes: List[str],
    dest_codes: List[str],
    start_day: str,
    end_day: str,
    max_stops: int = 1,
) -> tuple[Dict[str, Dict[str, Any]], Dict[tuple[str, str, str], Dict[str, Any]]]:
    clean_src = list(dict.fromkeys(c.strip().upper() for c in source_codes if c.strip()))
    clean_dst = list(dict.fromkeys(c.strip().upper() for c in dest_codes if c.strip()))
    if not clean_src or not clean_dst:
        return {}, {}

    if set(clean_src) & set(clean_dst):
        disjoint_pairs = build_disjoint_route_pairs(clean_src, clean_dst)
        if not disjoint_pairs:
            return {}, {}
        tasks = [
            _fetch_kiwi_month_single(
                s_list,
                d_list,
                start_day,
                end_day,
                max_stops=max_stops,
            )
            for s_list, d_list in disjoint_pairs
        ]
        if not tasks:
            return {}, {}
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged_days: Dict[str, Dict[str, Any]] = {}
        merged_routes: Dict[tuple[str, str, str], Dict[str, Any]] = {}
        for item in results:
            if not isinstance(item, tuple):
                continue
            d_map, r_map = item
            for day, ent in d_map.items():
                if day not in merged_days or ent["price_pln"] < merged_days[day]["price_pln"]:
                    merged_days[day] = ent
            for rk, ent in r_map.items():
                if rk not in merged_routes or ent["price_pln"] < merged_routes[rk]["price_pln"]:
                    merged_routes[rk] = ent
        return merged_days, merged_routes

    return await _fetch_kiwi_month_single(clean_src, clean_dst, start_day, end_day, max_stops=max_stops)


async def get_universal_monthly_calendar(
    origin_iata: str = "KRK",
    dest_query: str = "BGY",
    month: Optional[str] = None,
) -> Dict[str, Any]:
    month_ym, start_day, end_day = _month_bounds(month or "")

    clean_orig = (origin_iata or "KRK").strip().upper()
    if clean_orig in ("ALL", "WSZYSTKIE", "PL", "POLSKA"):
        origin_codes = list(POLISH_AIRPORTS.keys())
    else:
        orig_preset = next((p for p in WORLD_PRESETS if p["id"].upper() == clean_orig), None)
        if orig_preset and orig_preset.get("codes") and orig_preset["codes"] != "WORLD":
            origin_codes = [c.strip().upper() for c in orig_preset["codes"].split(",") if c.strip()]
        else:
            raw_codes = [c.strip().upper() for c in origin_iata.replace(";", ",").split(",") if c.strip()]
            if raw_codes and all(len(c) == 3 and c.isalpha() for c in raw_codes):
                origin_codes = raw_codes
            else:
                origin_codes = resolve_destination_iata_list(origin_iata)
            if not origin_codes:
                origin_codes = ["KRK"]

    # Check if dest_query matches a preset ID (e.g. "USA", "TH", "CY", "PL") or country/city name or IATA list
    preset_match = next((p for p in WORLD_PRESETS if p["id"].upper() == (dest_query or "").strip().upper()), None)
    if preset_match and preset_match["codes"] and preset_match["codes"] != "WORLD":
        dest_codes = [c.strip().upper() for c in preset_match["codes"].split(",") if c.strip()]
    else:
        dest_codes = resolve_destination_iata_list(dest_query)

    if not dest_codes:
        has_foreign = any(o not in POLISH_AIRPORTS for o in origin_codes)
        dest_codes = ["WAW", "KRK", "GDN", "WRO", "KTW", "POZ"] if has_foreign else ["BGY", "CIA", "BCN", "STN", "ATH", "MLA", "PFO"]

    cal_cache_key = (tuple(sorted(origin_codes)), tuple(sorted(dest_codes)), month_ym)
    now = time.monotonic()
    cached_cal = _CAL_CACHE.get(cal_cache_key)
    if cached_cal and (now - cached_cal[0]) < _CAL_CACHE_TTL:
        return dict(cached_cal[1])

    longhaul_set = {"JFK", "EWR", "MIA", "ORD", "LAX", "SFO", "BOS", "YYZ", "YUL", "CUN", "BKK", "HKT", "KBV", "CNX", "SIN", "KUL", "DPS", "CGK", "NRT", "HND"}
    max_stops = 2 if any(d in longhaul_set for d in dest_codes) else 1
    is_bulk_domestic = len(set(origin_codes) & set(dest_codes)) >= 3

    # Query Kiwi GraphQL for both outbound & inbound daily one-ways, plus exact roundtrips and Ryanair 1x1
    tasks = [
        _fetch_kiwi_month_direction(origin_codes, dest_codes, start_day, end_day, max_stops=max_stops),
        _fetch_kiwi_month_direction(dest_codes, origin_codes, start_day, end_day, max_stops=max_stops),
        asyncio.sleep(0, result=[])
        if is_bulk_domestic
        else search_kiwi_multi_airline(
            airports=origin_codes,
            date_from=start_day,
            date_to=end_day,
            trip_type="roundtrip",
            nights_min=2,
            nights_max=10,
            dest_iata_list=dest_codes,
            max_stops=max_stops,
            max_layover_hours=8,
            max_travel_hours=28,
            limit=30,
        ),
    ]
    use_ryanair = len(origin_codes) == 1 and len(dest_codes) == 1 and origin_codes[0] != dest_codes[0]
    if use_ryanair:
        tasks.append(get_ryanair_monthly_calendar(origin_codes[0], dest_codes[0], start_day))
    else:
        tasks.append(asyncio.sleep(0, result=None))

    kiwi_out_res, kiwi_in_res, kiwi_rt_list, ryanair_cal = await asyncio.gather(*tasks)
    kiwi_out_map, kiwi_out_routes = kiwi_out_res if isinstance(kiwi_out_res, tuple) else ({}, {})
    kiwi_in_map, kiwi_in_routes = kiwi_in_res if isinstance(kiwi_in_res, tuple) else ({}, {})

    # Merge Ryanair direct daily fares if cheaper or missing in Kiwi
    if isinstance(ryanair_cal, dict):
        for rd in ryanair_cal.get("outbound_days", []):
            day = rd["day"]
            links = build_booking_links(origin_codes[0], dest_codes[0], day)
            entry = {
                "day": day,
                "departure": rd.get("departure"),
                "arrival": rd.get("arrival"),
                "origin_iata": origin_codes[0],
                "origin_city": POLISH_AIRPORTS.get(origin_codes[0], {}).get("city", origin_codes[0]),
                "dest_iata": dest_codes[0],
                "dest_city": dest_codes[0],
                "carrier_name": "Ryanair",
                "stops_count": 0,
                "price_pln": rd["price_pln"],
                "booking_url": links["ryanair"],
            }
            if day not in kiwi_out_map or rd["price_pln"] < kiwi_out_map[day]["price_pln"]:
                kiwi_out_map[day] = entry
            rk = (day, origin_codes[0], dest_codes[0])
            if rk not in kiwi_out_routes or rd["price_pln"] < kiwi_out_routes[rk]["price_pln"]:
                kiwi_out_routes[rk] = entry

        for rd in ryanair_cal.get("inbound_days", []):
            day = rd["day"]
            links = build_booking_links(dest_codes[0], origin_codes[0], day)
            entry = {
                "day": day,
                "departure": rd.get("departure"),
                "arrival": rd.get("arrival"),
                "origin_iata": dest_codes[0],
                "origin_city": dest_codes[0],
                "dest_iata": origin_codes[0],
                "dest_city": POLISH_AIRPORTS.get(origin_codes[0], {}).get("city", origin_codes[0]),
                "carrier_name": "Ryanair",
                "stops_count": 0,
                "price_pln": rd["price_pln"],
                "booking_url": links["ryanair"],
            }
            if day not in kiwi_in_map or rd["price_pln"] < kiwi_in_map[day]["price_pln"]:
                kiwi_in_map[day] = entry
            rk = (day, dest_codes[0], origin_codes[0])
            if rk not in kiwi_in_routes or rd["price_pln"] < kiwi_in_routes[rk]["price_pln"]:
                kiwi_in_routes[rk] = entry

    outbound_days = sorted(kiwi_out_map.values(), key=lambda x: x["day"])
    inbound_days = sorted(kiwi_in_map.values(), key=lambda x: x["day"])

    # Compute best exact round-trip combinations (2 to 10 nights) on the SAME airport pair (no open-jaw!)
    combo_map: Dict[tuple, Dict[str, Any]] = {}

    for out_d in kiwi_out_routes.values():
        try:
            dt1 = datetime.fromisoformat(out_d["day"])
        except Exception:
            continue
        for in_d in kiwi_in_routes.values():
            # Enforce exact same airport pair for outbound and return
            if out_d["origin_iata"] != in_d["dest_iata"] or out_d["dest_iata"] != in_d["origin_iata"]:
                continue
            try:
                dt2 = datetime.fromisoformat(in_d["day"])
            except Exception:
                continue
            nights = (dt2 - dt1).days
            if 2 <= nights <= 10:
                total = round(out_d["price_pln"] + in_d["price_pln"], 2)
                links = build_booking_links(
                    out_d["origin_iata"],
                    out_d["dest_iata"],
                    out_d["day"],
                    in_d["day"],
                )
                booking_url = links["kiwi"]
                if (
                    out_d["carrier_name"] == "Ryanair"
                    and in_d["carrier_name"] == "Ryanair"
                    and out_d["stops_count"] == 0
                    and in_d["stops_count"] == 0
                ):
                    booking_url = links["ryanair"]

                ckey = (out_d["origin_iata"], out_d["dest_iata"], out_d["day"], in_d["day"])
                if ckey not in combo_map or total < combo_map[ckey]["total_price_pln"]:
                    combo_map[ckey] = {
                        "outbound_day": out_d["day"],
                        "inbound_day": in_d["day"],
                        "nights": nights,
                        "origin_iata": out_d["origin_iata"],
                        "dest_iata": out_d["dest_iata"],
                        "dest_city": out_d.get("dest_city", out_d["dest_iata"]),
                        "outbound_carrier": out_d["carrier_name"],
                        "inbound_carrier": in_d["carrier_name"],
                        "outbound_price_pln": out_d["price_pln"],
                        "inbound_price_pln": in_d["price_pln"],
                        "total_price_pln": total,
                        "booking_url": booking_url,
                    }

    # Also merge verified exact round-trip itineraries returned by Kiwi
    for rt in (kiwi_rt_list or []):
        out_day = (rt.get("outbound_departure") or "")[:10]
        in_day = (rt.get("inbound_departure") or "")[:10]
        if not out_day or not in_day:
            continue
        ckey = (rt["origin_iata"], rt["dest_iata"], out_day, in_day)
        total = round(float(rt["price_pln"]), 2)
        if ckey not in combo_map or total < combo_map[ckey]["total_price_pln"]:
            combo_map[ckey] = {
                "outbound_day": out_day,
                "inbound_day": in_day,
                "nights": int(rt.get("nights") or 2),
                "origin_iata": rt["origin_iata"],
                "dest_iata": rt["dest_iata"],
                "dest_city": rt.get("dest_city", rt["dest_iata"]),
                "outbound_carrier": rt.get("carrier_name", ""),
                "inbound_carrier": rt.get("return_carrier_name") or rt.get("carrier_name", ""),
                "outbound_price_pln": round(total / 2.0, 2),
                "inbound_price_pln": round(total - (total / 2.0), 2),
                "total_price_pln": total,
                "booking_url": rt["primary_booking_url"],
            }

    best_combos = sorted(combo_map.values(), key=lambda x: x["total_price_pln"])

    payload = {
        "origin_iata": clean_orig,
        "dest_codes": dest_codes,
        "month": month_ym,
        "outbound_days": outbound_days,
        "inbound_days": inbound_days,
        "best_combinations": best_combos[:14],
    }
    if outbound_days or inbound_days or best_combos:
        if len(_CAL_CACHE) >= 80:
            expired = [k for k, (ts, _) in _CAL_CACHE.items() if (now - ts) >= _CAL_CACHE_TTL]
            for ek in expired:
                _CAL_CACHE.pop(ek, None)
            if len(_CAL_CACHE) >= 80:
                _CAL_CACHE.pop(next(iter(_CAL_CACHE)), None)
        _CAL_CACHE[cal_cache_key] = (time.monotonic(), dict(payload))

    return payload
