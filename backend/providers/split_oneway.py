import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional

from backend.airports import build_booking_links, estimate_distance_km
from backend.providers.ryanair import search_ryanair_flights
from backend.providers.kiwi_multi import search_kiwi_multi_airline

# Popular international destination airports used as fast fallback when scanning WORLD one-way returns
POPULAR_RETURN_ORIGIN_POOLS = [
    "BCN", "AGP", "ALC", "PMI", "TFS", "FNC", "LIS",
    "CIA", "FCO", "BGY", "MXP", "NAP", "VCE",
    "ATH", "CHQ", "RHO", "MLA", "PFO", "LCA",
    "STN", "LTN", "BVA", "CRL", "EIN", "BUD", "TRF", "ARN", "OSL", "CPH", "AYT", "DXB",
]


def _parse_iso_dt(dt_str: str) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str[:19])
    except Exception:
        return None


async def build_split_oneway_flights(
    airports: List[str],
    date_from: str,
    date_to: str,
    nights_min: int = 2,
    nights_max: int = 12,
    max_price: Optional[int] = None,
    dest_iata_list: Optional[List[str]] = None,
    max_stops: int = 1,
    max_layover_hours: int = 6,
    max_travel_hours: int = 24,
) -> List[Dict[str, Any]]:
    """
    Combines direct Ryanair One-Way fares and Kiwi GraphQL One-Way fares (Wizz Air, Norwegian, easyJet, etc.)
    into zero-markup split round-trip pairs strictly matching the exact same origin <-> destination airports.
    """
    clean_airports = [a.strip().upper() for a in airports if a.strip()]
    if not clean_airports:
        return []
    clean_airports_set = set(clean_airports)
    clean_dests = [d.strip().upper() for d in (dest_iata_list or []) if d.strip()]
    single_dest = clean_dests[0] if len(clean_dests) == 1 else None

    eff_stops = max_stops if max_travel_hours >= 22 else min(max_stops, 1)
    is_bulk_domestic = len(set(clean_airports) & set(clean_dests)) >= 3

    # Step 1: Fetch all cheap one-way OUTBOUND legs (+ inbound immediately if dest_iata_list is known)
    tasks: List[Any] = [
        search_ryanair_flights(
            clean_airports, date_from, date_to, trip_type="oneway", max_price=max_price, dest_iata=single_dest
        ),
        asyncio.sleep(0, result=[])
        if is_bulk_domestic
        else search_kiwi_multi_airline(
            airports=clean_airports,
            date_from=date_from,
            date_to=date_to,
            trip_type="oneway",
            max_price=max_price,
            dest_iata_list=clean_dests if clean_dests else None,
            max_stops=eff_stops,
            max_layover_hours=max_layover_hours,
            max_travel_hours=max_travel_hours,
            limit=80,
        ),
    ]

    if clean_dests:
        # We already know destination airports; fetch inbound returns in the exact same parallel batch
        ryanair_in_sources = clean_dests[:12]
        missing_in_sources = [c for c in ryanair_in_sources if c not in clean_airports_set]
        if is_bulk_domestic and not missing_in_sources:
            tasks.append(asyncio.sleep(0, result=[]))
        else:
            tasks.append(
                search_ryanair_flights(
                    missing_in_sources if is_bulk_domestic else ryanair_in_sources,
                    date_from,
                    date_to,
                    trip_type="oneway",
                    max_price=max_price,
                )
            )
        tasks.append(
            asyncio.sleep(0, result=[])
            if is_bulk_domestic
            else search_kiwi_multi_airline(
                airports=clean_dests,
                date_from=date_from,
                date_to=date_to,
                trip_type="oneway",
                max_price=max_price,
                dest_iata_list=clean_airports,
                max_stops=eff_stops,
                max_layover_hours=max_layover_hours,
                max_travel_hours=max_travel_hours,
                limit=85,
            )
        )
        out_fr, out_kw, in_fr, in_kw = await asyncio.gather(*tasks, return_exceptions=True)
        out_all = (out_fr if isinstance(out_fr, list) else []) + (out_kw if isinstance(out_kw, list) else [])
        if is_bulk_domestic and not missing_in_sources:
            in_all = list(out_fr if isinstance(out_fr, list) else [])
        else:
            in_all = (in_fr if isinstance(in_fr, list) else []) + (in_kw if isinstance(in_kw, list) else [])
            if is_bulk_domestic and isinstance(out_fr, list):
                in_all.extend(out_fr)
    else:
        # WORLD search: first get cheapest outbound candidates, then immediately fetch returns from those destinations
        out_fr, out_kw = await asyncio.gather(*tasks, return_exceptions=True)
        out_all = (out_fr if isinstance(out_fr, list) else []) + (out_kw if isinstance(out_kw, list) else [])
        if not out_all:
            return []

        out_sorted = sorted(out_all, key=lambda x: x["price_pln"])
        discovered_dests: List[str] = []
        seen_dests = set()
        for f in out_sorted:
            dc = f.get("dest_iata", "").upper()
            if dc and dc not in seen_dests:
                seen_dests.add(dc)
                discovered_dests.append(dc)
            if len(discovered_dests) >= 24:
                break

        for hub in POPULAR_RETURN_ORIGIN_POOLS:
            if hub not in seen_dests and len(discovered_dests) < 30:
                seen_dests.add(hub)
                discovered_dests.append(hub)

        in_fr, in_kw = await asyncio.gather(
            search_ryanair_flights(discovered_dests[:12], date_from, date_to, trip_type="oneway", max_price=max_price),
            search_kiwi_multi_airline(
                airports=discovered_dests,
                date_from=date_from,
                date_to=date_to,
                trip_type="oneway",
                max_price=max_price,
                dest_iata_list=clean_airports,
                max_stops=eff_stops,
                max_layover_hours=max_layover_hours,
                max_travel_hours=max_travel_hours,
                limit=110,
            ),
            return_exceptions=True,
        )
        in_all = (in_fr if isinstance(in_fr, list) else []) + (in_kw if isinstance(in_kw, list) else [])

    if clean_dests:
        dest_filter_set = set(clean_dests)
        out_all = [f for f in out_all if f.get("dest_iata") in dest_filter_set]

    # Keep only valid Polish return destinations and index cheapest one-way legs per day on exact airport pair
    best_out_by_key: Dict[tuple, Dict[str, Any]] = {}
    for f in out_all:
        orig = f.get("origin_iata", "").upper()
        dest = f.get("dest_iata", "").upper()
        dep_day = (f.get("outbound_departure") or "")[:10]
        if orig not in clean_airports_set or not dest or not dep_day:
            continue
        k = (orig, dest, dep_day)
        if k not in best_out_by_key or f["price_pln"] < best_out_by_key[k]["price_pln"]:
            best_out_by_key[k] = f

    in_by_pair: Dict[tuple, List[Dict[str, Any]]] = {}
    best_in_by_key: Dict[tuple, Dict[str, Any]] = {}
    for f in in_all:
        in_orig = f.get("origin_iata", "").upper()   # departure from abroad
        in_dest = f.get("dest_iata", "").upper()     # arrival back in Poland
        dep_day = (f.get("outbound_departure") or "")[:10]
        if in_dest not in clean_airports_set or not in_orig or not dep_day:
            continue
        rk = (in_orig, in_dest, dep_day)
        if rk not in best_in_by_key or f["price_pln"] < best_in_by_key[rk]["price_pln"]:
            best_in_by_key[rk] = f

    for rk, f in best_in_by_key.items():
        pair_key = (rk[0], rk[1])  # (abroad_dest, pl_orig)
        in_by_pair.setdefault(pair_key, []).append(f)

    # Pair matching exact route (out: PL -> ABROAD, in: ABROAD -> PL)
    paired_deals: Dict[tuple, Dict[str, Any]] = {}
    for (orig, dest, out_day), out_f in best_out_by_key.items():
        matching_inbounds = in_by_pair.get((dest, orig), [])
        if not matching_inbounds:
            continue

        dt_out_dep = _parse_iso_dt(out_f.get("outbound_departure", ""))
        dt_out_arr = _parse_iso_dt(out_f.get("outbound_arrival", ""))
        if not dt_out_dep:
            continue

        for in_f in matching_inbounds:
            in_day = (in_f.get("outbound_departure") or "")[:10]
            dt_in_dep = _parse_iso_dt(in_f.get("outbound_departure", ""))
            if not dt_in_dep:
                continue

            nights = (dt_in_dep.date() - dt_out_dep.date()).days
            if nights < nights_min or nights > nights_max:
                continue

            # Guard minimum turn time on short stays
            if dt_out_arr and (dt_in_dep - dt_out_arr).total_seconds() < 14400:
                continue

            total_price = round(float(out_f["price_pln"]) + float(in_f["price_pln"]), 2)
            if max_price and total_price > max_price:
                continue

            out_c = out_f.get("carrier_name", "Linia")
            in_c = in_f.get("carrier_name", "Linia")
            is_mixed = out_c != in_c
            carrier_display = f"{out_c} + {in_c}" if is_mixed else out_c
            first_code = out_f.get("carrier_code") or "FR"
            ret_code = in_f.get("carrier_code") or first_code

            stops_out = int(out_f.get("stops_count") or 0)
            stops_in = int(in_f.get("stops_count") or 0)
            total_stops = max(stops_out, stops_in)
            if total_stops > max_stops:
                continue

            all_layovers = (out_f.get("layover_details") or []) + [
                {**l, "direction": "Powrót"} for l in (in_f.get("layover_details") or [])
            ]
            max_lay_sec = max((l.get("duration_sec", 0) for l in all_layovers), default=0)
            total_lay_sec = sum(l.get("duration_sec", 0) for l in all_layovers)

            links = build_booking_links(orig, dest, out_day, in_day, 1)
            dist = out_f.get("distance_km") or estimate_distance_km(orig, dest)

            # Direct airline links preferred over generic aggregator URLs
            out_url = out_f["primary_booking_url"]
            in_url = in_f["primary_booking_url"]

            if not is_mixed and first_code == "FR" and total_stops == 0:
                primary_url = links["ryanair"]
            else:
                primary_url = out_url

            lay_summary = "Lot bezpośredni (bez przesiadek)"
            if (stops_out + stops_in) > 0 and all_layovers:
                parts = [f"{l['city']} ({l['airport_code']}) – {l['duration_str']}" for l in all_layovers[:3]]
                lay_summary = f"Przesiadki ({len(all_layovers)}): {' • '.join(parts)}"

            pkey = (orig, dest, out_day, in_day, total_stops)
            if pkey in paired_deals and total_price >= paired_deals[pkey]["price_pln"]:
                continue

            paired_deals[pkey] = {
                "id": f"SP-RT-{orig}-{dest}-{out_day}-{in_day}-{first_code}_{ret_code}-{total_stops}",
                "trip_type": "roundtrip",
                "source_engine": "split_oneway",
                "source_label": "Składak 2×1 strona (Bez marży)",
                "is_split_ticket": True,
                "carrier_code": first_code,
                "carrier_name": carrier_display,
                "outbound_carrier": out_c,
                "inbound_carrier": in_c,
                "return_carrier_code": ret_code,
                "return_carrier_name": in_c,
                "is_mixed_carrier": is_mixed,
                "origin_iata": orig,
                "origin_city": out_f.get("origin_city", orig),
                "origin_name": out_f.get("origin_name", orig),
                "dest_iata": dest,
                "dest_city": out_f.get("dest_city", dest),
                "dest_name": out_f.get("dest_name", dest),
                "dest_country": out_f.get("dest_country", ""),
                "dest_country_code": out_f.get("dest_country_code", ""),
                "flag": out_f.get("flag", ""),
                "outbound_departure": out_f.get("outbound_departure"),
                "outbound_arrival": out_f.get("outbound_arrival"),
                "inbound_departure": in_f.get("outbound_departure"),
                "inbound_arrival": in_f.get("outbound_arrival"),
                "nights": nights,
                "stops_count": total_stops,
                "outbound_stops": stops_out,
                "inbound_stops": stops_in,
                "outbound_duration_sec": out_f.get("outbound_duration_sec", 0),
                "inbound_duration_sec": in_f.get("outbound_duration_sec", 0),
                "outbound_duration_str": out_f.get("outbound_duration_str", "—"),
                "inbound_duration_str": in_f.get("outbound_duration_str", "—"),
                "max_layover_sec": max_lay_sec,
                "max_layover_str": in_f.get("max_layover_str") if max_lay_sec else "Brak",
                "total_layover_sec": total_lay_sec,
                "layover_details": all_layovers,
                "layover_summary": lay_summary,
                "price_pln": total_price,
                "outbound_price_pln": round(float(out_f["price_pln"]), 2),
                "inbound_price_pln": round(float(in_f["price_pln"]), 2),
                "distance_km": dist,
                "flight_number": f"{out_f.get('flight_number', first_code)} / {in_f.get('flight_number', ret_code)}",
                "booking_links": links,
                "primary_booking_url": primary_url,
                "outbound_booking_url": out_url,
                "inbound_booking_url": in_url,
            }

    results = sorted(paired_deals.values(), key=lambda x: x["price_pln"])
    return results[:90]
