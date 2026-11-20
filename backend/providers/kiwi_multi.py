import asyncio
import time
import weakref
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import httpx

from backend.airports import (
    country_code_to_flag,
    estimate_distance_km,
    build_booking_links,
    format_duration_hm,
)

KIWI_GRAPHQL_URL = "https://api.skypicker.com/umbrella/v2/graphql"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

_KIWI_RESP_CACHE: Dict[tuple, tuple[float, List[Dict[str, Any]]]] = {}
_KIWI_CACHE_TTL = 150.0
_KIWI_CACHE_MAX = 120
_KIWI_LOOP_SEMS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _get_kiwi_sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _KIWI_LOOP_SEMS.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(5)
        _KIWI_LOOP_SEMS[loop] = sem
    return sem


def build_disjoint_route_pairs(
    clean_src: List[str], clean_dst: List[str]
) -> List[tuple[List[str], List[str]]]:
    """Build non-overlapping (source, dest) hub pairs so Kiwi GraphQL doesn't short-circuit to empty on domestic routes."""
    common = [c for c in clean_dst if c in clean_src]
    if not common:
        return [(clean_src, clean_dst)]

    priority_hubs = [h for h in ("WAW", "GDN", "KRK") if h in common]
    hubs = priority_hubs if priority_hubs else common[:3]

    pairs: List[tuple[List[str], List[str]]] = []
    covered: set[str] = set()
    for hub in hubs:
        fwd_dst = [d for d in clean_dst if d != hub and d not in covered]
        if hub in clean_src and fwd_dst:
            pairs.append(([hub], fwd_dst))
        bwd_src = [s for s in clean_src if s != hub and s not in covered]
        if hub in clean_dst and bwd_src:
            pairs.append((bwd_src, [hub]))
        covered.add(hub)
    return pairs

ONEWAY_QUERY = """
query SearchOneWayItinerariesQuery($search: SearchOnewayInput, $filter: ItinerariesFilterInput, $options: ItinerariesOptionsInput) {
  onewayItineraries(search: $search, filter: $filter, options: $options) {
    __typename
    ... on Itineraries {
      itineraries {
        id
        duration
        price { amount }
        bookingOptions {
          edges {
            node {
              bookingUrl
              price { amount }
            }
          }
        }
        ... on ItineraryOneWay {
          sector {
            duration
            sectorSegments {
              layover { duration }
              segment {
                duration
                source { station { code name city { name } country { code } } localTime }
                destination { station { code name city { name } country { code } } localTime }
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

RETURN_QUERY = """
query SearchReturnItinerariesQuery($search: SearchReturnInput, $filter: ItinerariesFilterInput, $options: ItinerariesOptionsInput) {
  returnItineraries(search: $search, filter: $filter, options: $options) {
    __typename
    ... on Itineraries {
      itineraries {
        id
        duration
        price { amount }
        bookingOptions {
          edges {
            node {
              bookingUrl
              price { amount }
            }
          }
        }
        ... on ItineraryReturn {
          outbound {
            duration
            sectorSegments {
              layover { duration }
              segment {
                duration
                source { station { code name city { name } country { code } } localTime }
                destination { station { code name city { name } country { code } } localTime }
                carrier { name code }
              }
            }
          }
          inbound {
            duration
            sectorSegments {
              layover { duration }
              segment {
                duration
                source { station { code name city { name } country { code } } localTime }
                destination { station { code name city { name } country { code } } localTime }
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


def _parse_sector_details(sector: Dict[str, Any], direction_label: str = "Tam") -> Dict[str, Any]:
    segs = sector.get("sectorSegments") or []
    first_seg = segs[0]["segment"]
    last_seg = segs[-1]["segment"]

    carriers: List[str] = []
    carrier_codes: List[str] = []
    layovers: List[Dict[str, Any]] = []
    has_station_change = False

    for idx, s_wrap in enumerate(segs):
        seg = s_wrap["segment"]
        c_name = seg["carrier"]["name"]
        c_code = seg["carrier"]["code"]
        if c_name not in carriers:
            carriers.append(c_name)
        if c_code not in carrier_codes:
            carrier_codes.append(c_code)

        # Check for hidden airport/station change during a self-transfer layover
        if idx < len(segs) - 1:
            next_seg = segs[idx + 1]["segment"]
            arr_code = (seg["destination"]["station"].get("code") or "").upper()
            next_dep_code = (next_seg["source"]["station"].get("code") or "").upper()
            if arr_code and next_dep_code and arr_code != next_dep_code:
                has_station_change = True

        # If there is a next segment, the destination of current segment is the layover airport
        lay_obj = s_wrap.get("layover")
        if lay_obj and lay_obj.get("duration") and idx < len(segs) - 1:
            lay_sec = int(lay_obj["duration"])
            stop_station = seg["destination"]["station"]
            layovers.append({
                "direction": direction_label,
                "airport_code": stop_station.get("code", ""),
                "city": (stop_station.get("city") or {}).get("name") or stop_station.get("name", ""),
                "duration_sec": lay_sec,
                "duration_str": format_duration_hm(lay_sec),
            })

    return {
        "first_seg": first_seg,
        "last_seg": last_seg,
        "stops_count": max(0, len(segs) - 1),
        "duration_sec": int(sector.get("duration") or 0),
        "carriers": carriers,
        "carrier_codes": carrier_codes,
        "layovers": layovers,
        "has_station_change": has_station_change,
    }


def _build_layover_summary(total_stops: int, layovers: List[Dict[str, Any]]) -> str:
    if total_stops == 0 or not layovers:
        return "Lot bezpośredni (bez przesiadek)"
    parts = [f"{l['city']} ({l['airport_code']}) – {l['duration_str']}" for l in layovers[:3]]
    prefix = "1 przesiadka:" if len(layovers) == 1 else f"Przesiadki ({len(layovers)}):"
    return f"{prefix} {' • '.join(parts)}"


async def _run_single_kiwi_query(
    airports: List[str],
    date_from: str,
    date_to: str,
    trip_type: str = "roundtrip",
    nights_min: int = 2,
    nights_max: int = 5,
    max_price: Optional[int] = None,
    dest_iata_list: Optional[List[str]] = None,
    max_stops: int = 1,
    max_layover_hours: int = 6,
    max_travel_hours: int = 16,
    limit: int = 55,
    outbound_start_dt: Optional[str] = None,
    outbound_end_dt: Optional[str] = None,
    inbound_start_dt: Optional[str] = None,
    inbound_end_dt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    source_ids = [f"Station:airport:{code.strip().upper()}" for code in airports if code.strip()]
    if not source_ids:
        return []

    itinerary_input: Dict[str, Any] = {
        "source": {"ids": source_ids},
        "outboundDepartureDate": {
            "start": outbound_start_dt or f"{date_from[:10]}T00:00:00",
            "end": outbound_end_dt or f"{date_to[:10]}T23:59:59",
        },
    }
    if dest_iata_list:
        itinerary_input["destination"] = {
            "ids": [f"Station:airport:{c.strip().upper()}" for c in dest_iata_list if c.strip()]
        }

    filter_input: Dict[str, Any] = {
        "limit": limit,
        "maxStopsCount": max(0, min(3, int(max_stops))),
    }
    if max_travel_hours and max_travel_hours > 0:
        filter_input["maxDuration"] = int(max_travel_hours)
    # Note: we enforce max_layover_hours accurately in Python below;
    # sending Kiwi's stopoverTime filter cuts valid non-stop / multi-carrier fares.

    if trip_type != "oneway":
        itinerary_input["inboundDepartureDate"] = {
            "start": inbound_start_dt or f"{date_from[:10]}T00:00:00",
            "end": inbound_end_dt or f"{date_to[:10]}T23:59:59",
        }
        filter_input["allowReturnToDifferentCity"] = False
        filter_input["allowReturnFromDifferentCity"] = False
        filter_input["allowChangeInboundSource"] = False
        filter_input["allowChangeInboundDestination"] = False

    feature_name = "SearchOneWayItinerariesQuery" if trip_type == "oneway" else "SearchReturnItinerariesQuery"
    query_str = ONEWAY_QUERY if trip_type == "oneway" else RETURN_QUERY

    payload = {
        "query": query_str,
        "variables": {
            "search": {
                "itinerary": itinerary_input,
                "passengers": {"adults": 1, "children": 0, "infants": 0},
            },
            "filter": filter_input,
            "options": {
                "currency": "PLN",
                "locale": "pl",
                "sortBy": "PRICE",
                "partner": "skypicker",
            },
        },
    }

    cache_key = (
        trip_type,
        tuple(sorted(source_ids)),
        tuple(sorted(dest_iata_list or [])),
        date_from[:10],
        date_to[:10],
        nights_min,
        nights_max,
        max_stops,
        max_travel_hours,
        max_layover_hours,
        limit,
        outbound_start_dt or "",
        outbound_end_dt or "",
        inbound_start_dt or "",
        inbound_end_dt or "",
    )
    now = time.monotonic()
    cached_entry = _KIWI_RESP_CACHE.get(cache_key)
    if cached_entry and (now - cached_entry[0]) < _KIWI_CACHE_TTL:
        cached_list = [dict(item) for item in cached_entry[1]]
        if max_price:
            return [x for x in cached_list if x["price_pln"] <= max_price]
        return cached_list

    sem = _get_kiwi_sem()
    async with sem:
        async with httpx.AsyncClient(headers=HEADERS) as client:
            try:
                resp = await client.post(
                    f"{KIWI_GRAPHQL_URL}?featureName={feature_name}",
                    json=payload,
                    timeout=15.0,
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
            except Exception:
                return []

    results: List[Dict[str, Any]] = []
    max_layover_limit_sec = int(max_layover_hours) * 3600 if max_layover_hours else 999999
    max_travel_limit_sec = int(max_travel_hours) * 3600 if max_travel_hours else 999999

    if trip_type == "oneway":
        itins = (data.get("data") or {}).get("onewayItineraries", {}).get("itineraries", [])
        for it in itins:
            try:
                price_val = float(it["price"]["amount"])
                if max_price and price_val > max_price:
                    continue

                sec_info = _parse_sector_details(it["sector"], "Tam")
                if sec_info["has_station_change"]:
                    continue
                out_dur_sec = sec_info["duration_sec"]
                layovers = sec_info["layovers"]
                max_lay_sec = max((l["duration_sec"] for l in layovers), default=0)
                total_lay_sec = sum(l["duration_sec"] for l in layovers)

                if out_dur_sec > max_travel_limit_sec or max_lay_sec > max_layover_limit_sec:
                    continue

                src = sec_info["first_seg"]["source"]["station"]
                dst = sec_info["last_seg"]["destination"]["station"]
                dep_dt = sec_info["first_seg"]["source"]["localTime"]
                arr_dt = sec_info["last_seg"]["destination"]["localTime"]

                orig_code = src["code"].upper()
                dest_code = dst["code"].upper()
                if not orig_code or not dest_code or orig_code == dest_code:
                    continue
                country_code = (dst.get("country") or {}).get("code", "")

                edges = (it.get("bookingOptions") or {}).get("edges") or []
                booking_path = edges[0]["node"].get("bookingUrl") if edges else None
                links = build_booking_links(orig_code, dest_code, dep_dt, None, 1, booking_path)
                dist = estimate_distance_km(orig_code, dest_code)

                carrier_display = " + ".join(sec_info["carriers"][:2])
                first_code = sec_info["carrier_codes"][0]
                is_mixed = len(sec_info["carrier_codes"]) > 1

                primary_url = links["kiwi"]
                if sec_info["stops_count"] == 0 and first_code == "FR":
                    primary_url = links["ryanair"]

                results.append({
                    "id": f"KW-OW-{orig_code}-{dest_code}-{dep_dt[:10]}-{first_code}-{sec_info['stops_count']}",
                    "trip_type": "oneway",
                    "source_engine": "kiwi_multi",
                    "source_label": f"Multi-Air ({carrier_display})",
                    "carrier_code": first_code,
                    "carrier_name": carrier_display,
                    "return_carrier_code": None,
                    "return_carrier_name": None,
                    "is_mixed_carrier": is_mixed,
                    "origin_iata": orig_code,
                    "origin_city": (src.get("city") or {}).get("name", orig_code),
                    "origin_name": src.get("name", orig_code),
                    "dest_iata": dest_code,
                    "dest_city": (dst.get("city") or {}).get("name", dest_code),
                    "dest_name": dst.get("name", dest_code),
                    "dest_country": country_code,
                    "dest_country_code": country_code.upper(),
                    "flag": country_code_to_flag(country_code),
                    "outbound_departure": dep_dt,
                    "outbound_arrival": arr_dt,
                    "inbound_departure": None,
                    "inbound_arrival": None,
                    "nights": 0,
                    "stops_count": sec_info["stops_count"],
                    "outbound_stops": sec_info["stops_count"],
                    "inbound_stops": 0,
                    "outbound_duration_sec": out_dur_sec,
                    "inbound_duration_sec": 0,
                    "outbound_duration_str": format_duration_hm(out_dur_sec),
                    "inbound_duration_str": None,
                    "max_layover_sec": max_lay_sec,
                    "max_layover_str": format_duration_hm(max_lay_sec) if max_lay_sec > 0 else "Brak",
                    "total_layover_sec": total_lay_sec,
                    "layover_details": layovers,
                    "layover_summary": _build_layover_summary(sec_info["stops_count"], layovers),
                    "price_pln": round(price_val, 2),
                    "outbound_price_pln": round(price_val, 2),
                    "inbound_price_pln": None,
                    "distance_km": dist,
                    "flight_number": "/".join(sec_info["carrier_codes"]),
                    "booking_links": links,
                    "primary_booking_url": primary_url,
                })
            except Exception:
                continue
    else:
        itins = (data.get("data") or {}).get("returnItineraries", {}).get("itineraries", [])
        for it in itins:
            try:
                price_val = float(it["price"]["amount"])
                if max_price and price_val > max_price:
                    continue

                out_info = _parse_sector_details(it["outbound"], "Tam")
                in_info = _parse_sector_details(it["inbound"], "Powrót")
                if out_info["has_station_change"] or in_info["has_station_change"]:
                    continue

                out_dur_sec = out_info["duration_sec"]
                in_dur_sec = in_info["duration_sec"]
                all_layovers = out_info["layovers"] + in_info["layovers"]
                max_lay_sec = max((l["duration_sec"] for l in all_layovers), default=0)
                total_lay_sec = sum(l["duration_sec"] for l in all_layovers)

                if (
                    out_dur_sec > max_travel_limit_sec
                    or in_dur_sec > max_travel_limit_sec
                    or max_lay_sec > max_layover_limit_sec
                ):
                    continue

                src = out_info["first_seg"]["source"]["station"]
                dst = out_info["last_seg"]["destination"]["station"]
                in_src = in_info["first_seg"]["source"]["station"]
                in_dst = in_info["last_seg"]["destination"]["station"]

                orig_code = (src.get("code") or "").upper()
                dest_code = (dst.get("code") or "").upper()
                in_orig_code = (in_src.get("code") or "").upper()
                in_dest_code = (in_dst.get("code") or "").upper()

                # Reject open-jaw or same-airport itineraries
                if not orig_code or not dest_code or orig_code == dest_code or in_orig_code != dest_code or in_dest_code != orig_code:
                    continue

                dep_dt = out_info["first_seg"]["source"]["localTime"]
                arr_dt = out_info["last_seg"]["destination"]["localTime"]
                in_dep_dt = in_info["first_seg"]["source"]["localTime"]
                in_arr_dt = in_info["last_seg"]["destination"]["localTime"]

                d1 = datetime.fromisoformat(dep_dt[:10])
                d2 = datetime.fromisoformat(in_dep_dt[:10])
                nights = max(0, (d2 - d1).days)
                if nights < nights_min or nights > nights_max:
                    continue

                country_code = (dst.get("country") or {}).get("code", "")

                edges = (it.get("bookingOptions") or {}).get("edges") or []
                booking_path = edges[0]["node"].get("bookingUrl") if edges else None
                links = build_booking_links(orig_code, dest_code, dep_dt, in_dep_dt, 1, booking_path)
                dist = estimate_distance_km(orig_code, dest_code)

                all_carriers = list(dict.fromkeys(out_info["carriers"] + in_info["carriers"]))
                all_codes = list(dict.fromkeys(out_info["carrier_codes"] + in_info["carrier_codes"]))
                is_mixed = len(all_codes) > 1
                carrier_display = " + ".join(all_carriers[:2])

                total_stops = max(out_info["stops_count"], in_info["stops_count"])
                primary_url = links["kiwi"]
                if total_stops == 0 and not is_mixed and all_codes[0] == "FR":
                    primary_url = links["ryanair"]

                results.append({
                    "id": f"KW-RT-{orig_code}-{dest_code}-{dep_dt[:10]}-{in_dep_dt[:10]}-{'_'.join(all_codes)}-{total_stops}",
                    "trip_type": "roundtrip",
                    "source_engine": "kiwi_multi",
                    "source_label": f"Multi-Air ({carrier_display})",
                    "carrier_code": all_codes[0],
                    "carrier_name": carrier_display,
                    "return_carrier_code": in_info["carrier_codes"][0],
                    "return_carrier_name": " + ".join(in_info["carriers"][:2]),
                    "is_mixed_carrier": is_mixed,
                    "origin_iata": orig_code,
                    "origin_city": (src.get("city") or {}).get("name", orig_code),
                    "origin_name": src.get("name", orig_code),
                    "dest_iata": dest_code,
                    "dest_city": (dst.get("city") or {}).get("name", dest_code),
                    "dest_name": dst.get("name", dest_code),
                    "dest_country": country_code,
                    "dest_country_code": country_code.upper(),
                    "flag": country_code_to_flag(country_code),
                    "outbound_departure": dep_dt,
                    "outbound_arrival": arr_dt,
                    "inbound_departure": in_dep_dt,
                    "inbound_arrival": in_arr_dt,
                    "nights": nights,
                    "stops_count": total_stops,
                    "outbound_stops": out_info["stops_count"],
                    "inbound_stops": in_info["stops_count"],
                    "outbound_duration_sec": out_dur_sec,
                    "inbound_duration_sec": in_dur_sec,
                    "outbound_duration_str": format_duration_hm(out_dur_sec),
                    "inbound_duration_str": format_duration_hm(in_dur_sec),
                    "max_layover_sec": max_lay_sec,
                    "max_layover_str": format_duration_hm(max_lay_sec) if max_lay_sec > 0 else "Brak",
                    "total_layover_sec": total_lay_sec,
                    "layover_details": all_layovers,
                    "layover_summary": _build_layover_summary(
                        out_info["stops_count"] + in_info["stops_count"], all_layovers
                    ),
                    "price_pln": round(price_val, 2),
                    "outbound_price_pln": None,
                    "inbound_price_pln": None,
                    "distance_km": dist,
                    "flight_number": " / ".join(all_codes),
                    "booking_links": links,
                    "primary_booking_url": primary_url,
                })
            except Exception:
                continue

    if results:
        if len(_KIWI_RESP_CACHE) >= _KIWI_CACHE_MAX:
            expired = [k for k, (ts, _) in _KIWI_RESP_CACHE.items() if (now - ts) >= _KIWI_CACHE_TTL]
            for ek in expired:
                _KIWI_RESP_CACHE.pop(ek, None)
            if len(_KIWI_RESP_CACHE) >= _KIWI_CACHE_MAX:
                _KIWI_RESP_CACHE.pop(next(iter(_KIWI_RESP_CACHE)), None)
        _KIWI_RESP_CACHE[cache_key] = (now, [dict(item) for item in results])

    return results


async def search_kiwi_multi_airline(
    airports: List[str],
    date_from: str,
    date_to: str,
    trip_type: str = "roundtrip",
    nights_min: int = 2,
    nights_max: int = 5,
    max_price: Optional[int] = None,
    dest_iata_list: Optional[List[str]] = None,
    max_stops: int = 1,
    max_layover_hours: int = 6,
    max_travel_hours: int = 16,
    limit: int = 55,
    outbound_start_dt: Optional[str] = None,
    outbound_end_dt: Optional[str] = None,
    inbound_start_dt: Optional[str] = None,
    inbound_end_dt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    clean_src = list(dict.fromkeys(c.strip().upper() for c in airports if c.strip()))
    if not clean_src:
        return []
    clean_dst = list(dict.fromkeys(c.strip().upper() for c in (dest_iata_list or []) if c.strip()))

    if clean_dst and (set(clean_src) & set(clean_dst)):
        if len(clean_dst) == 1:
            non_overlap_src = [a for a in clean_src if a != clean_dst[0]]
            if not non_overlap_src:
                return []
            return await _run_single_kiwi_query(
                airports=non_overlap_src,
                date_from=date_from,
                date_to=date_to,
                trip_type=trip_type,
                nights_min=nights_min,
                nights_max=nights_max,
                max_price=max_price,
                dest_iata_list=clean_dst,
                max_stops=max_stops,
                max_layover_hours=max_layover_hours,
                max_travel_hours=max_travel_hours,
                limit=limit,
                outbound_start_dt=outbound_start_dt,
                outbound_end_dt=outbound_end_dt,
                inbound_start_dt=inbound_start_dt,
                inbound_end_dt=inbound_end_dt,
            )

        # Use minimal disjoint hub pairs so Kiwi doesn't return 0 or rate-limit on domestic PL routes
        disjoint_pairs = build_disjoint_route_pairs(clean_src, clean_dst)
        shard_limit = max(18, min(30, limit))
        tasks = [
            _run_single_kiwi_query(
                airports=s_list,
                date_from=date_from,
                date_to=date_to,
                trip_type=trip_type,
                nights_min=nights_min,
                nights_max=nights_max,
                max_price=max_price,
                dest_iata_list=d_list,
                max_stops=max_stops,
                max_layover_hours=max_layover_hours,
                max_travel_hours=max_travel_hours,
                limit=shard_limit,
                outbound_start_dt=outbound_start_dt,
                outbound_end_dt=outbound_end_dt,
                inbound_start_dt=inbound_start_dt,
                inbound_end_dt=inbound_end_dt,
            )
            for s_list, d_list in disjoint_pairs
        ]
        if not tasks:
            return []
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        dedup: Dict[str, Dict[str, Any]] = {}
        for chunk in gathered:
            if not isinstance(chunk, list):
                continue
            for item in chunk:
                fid = item.get("id")
                if fid and (fid not in dedup or item["price_pln"] < dedup[fid]["price_pln"]):
                    dedup[fid] = item
        return sorted(dedup.values(), key=lambda x: x["price_pln"])

    return await _run_single_kiwi_query(
        airports=clean_src,
        date_from=date_from,
        date_to=date_to,
        trip_type=trip_type,
        nights_min=nights_min,
        nights_max=nights_max,
        max_price=max_price,
        dest_iata_list=clean_dst if clean_dst else None,
        max_stops=max_stops,
        max_layover_hours=max_layover_hours,
        max_travel_hours=max_travel_hours,
        limit=limit,
        outbound_start_dt=outbound_start_dt,
        outbound_end_dt=outbound_end_dt,
        inbound_start_dt=inbound_start_dt,
        inbound_end_dt=inbound_end_dt,
    )


async def search_kiwi_weekend_windows(
    airports: List[str],
    date_from: str,
    date_to: str,
    trip_type: str = "roundtrip",
    max_price: Optional[int] = None,
    dest_iata_list: Optional[List[str]] = None,
    max_stops: int = 1,
    max_layover_hours: int = 4,
    max_travel_hours: int = 10,
) -> List[Dict[str, Any]]:
    """Queries the next 4 weekend windows directly so weekend deals aren't crowded out by Tuesday/Wednesday fares."""
    try:
        d_start = datetime.fromisoformat(date_from[:10]).date()
        d_end = datetime.fromisoformat(date_to[:10]).date()
    except Exception:
        d_start = datetime.now().date() + timedelta(days=2)
        d_end = d_start + timedelta(days=35)

    windows = []
    cur = d_start
    while cur <= d_end and len(windows) < 4:
        if cur.weekday() == 3:  # Thursday
            thu = cur
            sat = cur + timedelta(days=2)
            sun = cur + timedelta(days=3)
            mon = cur + timedelta(days=4)
            windows.append((thu, sat, sun, mon))
        cur += timedelta(days=1)

    if not windows:
        return []

    tasks = []
    for thu, sat, sun, mon in windows:
        # Query Fri >=14:30 -> Sat 13:30 as well as Thu 16:00 -> Sat 13:30
        tasks.append(
            search_kiwi_multi_airline(
                airports=airports,
                date_from=thu.isoformat(),
                date_to=mon.isoformat(),
                trip_type=trip_type,
                nights_min=1,
                nights_max=4,
                max_price=max_price,
                dest_iata_list=dest_iata_list,
                max_stops=max_stops,
                max_layover_hours=max_layover_hours,
                max_travel_hours=max_travel_hours,
                limit=35,
                outbound_start_dt=f"{thu.isoformat()}T16:00:00",
                outbound_end_dt=f"{sat.isoformat()}T13:30:00",
                inbound_start_dt=f"{sun.isoformat()}T13:00:00",
                inbound_end_dt=f"{mon.isoformat()}T08:00:00",
            )
        )
        # Also query specifically Fri 14:30 -> Sat 13:30 so Friday-afternoon flights aren't edged out by Thursday flights
        fri = thu + timedelta(days=1)
        tasks.append(
            search_kiwi_multi_airline(
                airports=airports,
                date_from=fri.isoformat(),
                date_to=mon.isoformat(),
                trip_type=trip_type,
                nights_min=1,
                nights_max=3,
                max_price=max_price,
                dest_iata_list=dest_iata_list,
                max_stops=max_stops,
                max_layover_hours=max_layover_hours,
                max_travel_hours=max_travel_hours,
                limit=25,
                outbound_start_dt=f"{fri.isoformat()}T14:30:00",
                outbound_end_dt=f"{sat.isoformat()}T13:30:00",
                inbound_start_dt=f"{sun.isoformat()}T13:00:00",
                inbound_end_dt=f"{mon.isoformat()}T08:00:00",
            )
        )

    grouped = await asyncio.gather(*tasks, return_exceptions=True)
    merged: List[Dict[str, Any]] = []
    for item in grouped:
        if isinstance(item, list):
            merged.extend(item)
    return merged
