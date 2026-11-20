import asyncio
from collections import defaultdict
from typing import List, Dict, Any, Optional

from backend.airports import (
    POLISH_AIRPORTS,
    resolve_destination_iata_list,
    get_destination_weather,
    get_destination_cost_info,
    estimate_baggage_fee_pln,
    is_weekend_friendly,
    is_comfortable_hours,
    build_lodging_links,
)
from backend.providers.ryanair import search_ryanair_flights
from backend.providers.kiwi_multi import search_kiwi_multi_airline, search_kiwi_weekend_windows
from backend.providers.split_oneway import build_split_oneway_flights
from backend.providers.amadeus_gds import search_amadeus_flights, is_amadeus_configured
from backend.providers.travelpayouts import search_travelpayouts_flights, is_travelpayouts_configured
from backend.database import update_and_get_route_stats, get_route_history_summary

WORLD_REGIONAL_BUCKETS = [
    {
        "codes": [
            "BCN", "AGP", "ALC", "PMI", "TFS", "LPA", "FAO", "LIS", "OPO", "FNC",
            "CIA", "FCO", "NAP", "CTA", "PMO", "ATH", "SKG", "HER", "CHQ", "RHO", "PFO", "LCA", "MLA",
        ],
        "limit": 35,
        "longhaul": False,
    },
    {
        "codes": [
            "STN", "LTN", "LGW", "MAN", "EDI", "DUB", "BVA", "ORY", "NCE", "MRS",
            "CRL", "EIN", "AMS", "BER", "FRA", "MUC", "CPH", "OSL", "BGO", "KEF",
        ],
        "limit": 30,
        "longhaul": False,
    },
    {
        "codes": [
            "AYT", "IST", "TBS", "DXB", "AUH", "RAK", "AGA", "HRG", "RMF", "AMM",
            "JFK", "MIA", "ORD", "LAX", "YYZ", "CUN", "BKK", "HKT", "SIN", "DPS", "NRT", "HND",
        ],
        "limit": 35,
        "longhaul": True,
    },
]


def _compute_time_and_deal_scores(
    flight: Dict[str, Any],
    dest_avg_price: float,
    hist_avg_price: float,
) -> tuple[int, str, int, str]:
    price = flight["price_pln"]
    trip_type = flight["trip_type"]
    dist = flight.get("distance_km") or 1100
    stops = flight.get("stops_count") or 0
    max_lay_sec = flight.get("max_layover_sec") or 0
    max_lay_h = max_lay_sec / 3600.0

    # 1. Time Respect Score (0 - 100)
    if stops == 0:
        time_score = 98
        time_badge = "Lot bezpośredni"
    elif stops == 1:
        if max_lay_h <= 2.5:
            time_score = 89
            time_badge = f"Szybka przesiadka ({flight.get('max_layover_str')})"
        elif max_lay_h <= 4.5:
            time_score = 78
            time_badge = f"Wygodna przesiadka ({flight.get('max_layover_str')})"
        else:
            time_score = max(45, int(75 - (max_lay_h - 4.5) * 6))
            time_badge = f"Przesiadka ({flight.get('max_layover_str')})"
    else:
        time_score = max(35, int(68 - max_lay_h * 4))
        time_badge = f"{stops} przesiadki (max {flight.get('max_layover_str')})"

    # 2. Price efficiency relative to distance
    total_km = max(300, dist * (2 if trip_type == "roundtrip" else 1))
    pln_per_100km = (price / total_km) * 100.0

    if dist >= 3500:
        if pln_per_100km <= 18:
            base_price_score = 94
        elif pln_per_100km <= 25:
            base_price_score = 86
        elif pln_per_100km <= 34:
            base_price_score = 76
        else:
            base_price_score = 64
    else:
        if trip_type == "oneway":
            if price <= 65:
                base_price_score = 93
            elif price <= 110:
                base_price_score = 84
            elif price <= 180:
                base_price_score = 74
            else:
                base_price_score = max(40, int(70 - (price - 180) * 0.12))
        else:
            if price <= 145:
                base_price_score = 94
            elif price <= 220:
                base_price_score = 85
            elif price <= 350:
                base_price_score = 76
            elif price <= 550:
                base_price_score = 68
            else:
                base_price_score = max(40, int(65 - (price - 550) * 0.05))

    if pln_per_100km < 9.0:
        base_price_score += 6
    elif pln_per_100km < 14.0:
        base_price_score += 3

    if dest_avg_price > price:
        diff_pct = (dest_avg_price - price) / dest_avg_price
        base_price_score += min(6, int(diff_pct * 20))

    if hist_avg_price > price:
        base_price_score += 2

    combined = int(round(base_price_score * 0.72 + time_score * 0.28))
    combined = max(15, min(99, combined))

    if combined >= 86:
        badge = "Świetna okazja"
    elif combined >= 75:
        badge = "Bardzo dobra cena"
    elif combined >= 62:
        badge = "Dobra oferta"
    else:
        badge = "Standardowa cena"

    return combined, badge, time_score, time_badge


async def scan_and_compare_flights(
    airports: List[str],
    date_from: str,
    date_to: str,
    trip_type: str = "roundtrip",
    nights_min: int = 2,
    nights_max: int = 12,
    max_price: Optional[int] = None,
    dest_query: Optional[str] = None,
    group_size: int = 1,
    max_stops: int = 2,
    max_layover_hours: int = 6,
    max_travel_hours: int = 26,
    sort_by: str = "price",          # "price", "smart", "total_trip"
    weekend_only: bool = False,
    warm_only: bool = False,
    comfortable_hours: bool = False,
    baggage_mode: str = "backpack",
    sources: str = "all",
) -> Dict[str, Any]:
    clean_airports = [a.strip().upper() for a in airports if a.strip()]
    if not clean_airports:
        clean_airports = ["WAW", "WMI", "KRK", "KTW", "GDN", "WRO", "POZ", "RZE", "LCJ", "SZZ", "BZG", "LUZ"]

    is_whole_world = bool(dest_query and dest_query.strip().lower() in ("world", "cały świat", "caly swiat", "świat"))
    resolved_iata_list = [] if is_whole_world else resolve_destination_iata_list(dest_query)
    single_dest_iata = resolved_iata_list[0] if len(resolved_iata_list) == 1 else None

    eff_nights_min = 1 if weekend_only else nights_min
    eff_nights_max = min(nights_max, 3) if weekend_only else nights_max

    ryanair_task = (
        search_ryanair_flights(
            clean_airports, date_from, date_to, trip_type, eff_nights_min, eff_nights_max, max_price, single_dest_iata
        )
        if sources in ("all", "ryanair")
        else asyncio.sleep(0, result=[])
    )

    kiwi_tasks: List[Any] = []
    if sources in ("all", "kiwi"):
        if weekend_only:
            kiwi_tasks.append(
                search_kiwi_weekend_windows(
                    airports=clean_airports,
                    date_from=date_from,
                    date_to=date_to,
                    trip_type=trip_type,
                    max_price=max_price,
                    dest_iata_list=resolved_iata_list if resolved_iata_list else None,
                    max_stops=max_stops,
                    max_layover_hours=max_layover_hours,
                    max_travel_hours=max_travel_hours,
                )
            )
        else:
            kiwi_tasks.append(
                search_kiwi_multi_airline(
                    airports=clean_airports,
                    date_from=date_from,
                    date_to=date_to,
                    trip_type=trip_type,
                    nights_min=eff_nights_min,
                    nights_max=eff_nights_max,
                    max_price=max_price,
                    dest_iata_list=resolved_iata_list if resolved_iata_list else None,
                    max_stops=max_stops,
                    max_layover_hours=max_layover_hours,
                    max_travel_hours=max_travel_hours,
                    limit=45 if is_whole_world else 55,
                )
            )
            if is_whole_world:
                for bucket in WORLD_REGIONAL_BUCKETS:
                    b_n_min = max(eff_nights_min, 4) if bucket["longhaul"] else eff_nights_min
                    b_n_max = max(eff_nights_max, 12) if bucket["longhaul"] else eff_nights_max
                    b_stops = max(max_stops, 1) if bucket["longhaul"] else max_stops
                    b_hours = max(max_travel_hours, 26) if bucket["longhaul"] else max_travel_hours
                    kiwi_tasks.append(
                        search_kiwi_multi_airline(
                            airports=clean_airports,
                            date_from=date_from,
                            date_to=date_to,
                            trip_type=trip_type,
                            nights_min=b_n_min,
                            nights_max=b_n_max,
                            max_price=max_price,
                            dest_iata_list=bucket["codes"],
                            max_stops=b_stops,
                            max_layover_hours=max_layover_hours,
                            max_travel_hours=b_hours,
                            limit=bucket["limit"],
                        )
                    )
                if any(a not in POLISH_AIRPORTS for a in clean_airports):
                    kiwi_tasks.append(
                        search_kiwi_multi_airline(
                            airports=clean_airports,
                            date_from=date_from,
                            date_to=date_to,
                            trip_type=trip_type,
                            nights_min=eff_nights_min,
                            nights_max=eff_nights_max,
                            max_price=max_price,
                            dest_iata_list=["WAW", "KRK", "GDN", "WRO", "KTW", "POZ", "RZE", "SZZ"],
                            max_stops=max(max_stops, 1),
                            max_layover_hours=max_layover_hours,
                            max_travel_hours=max_travel_hours,
                            limit=20,
                        )
                    )
                elif "WAW" in clean_airports:
                    kiwi_tasks.append(
                        search_kiwi_multi_airline(
                            airports=["WAW"],
                            date_from=date_from,
                            date_to=date_to,
                            trip_type=trip_type,
                            nights_min=eff_nights_min,
                            nights_max=eff_nights_max,
                            max_price=max_price,
                            dest_iata_list=["GDN", "KRK", "WRO", "POZ", "KTW", "RZE", "SZZ", "LUZ", "BZG"],
                            max_stops=0,
                            max_layover_hours=max_layover_hours,
                            max_travel_hours=min(max_travel_hours, 4),
                            limit=14,
                        )
                    )

    split_task = (
        build_split_oneway_flights(
            airports=clean_airports,
            date_from=date_from,
            date_to=date_to,
            nights_min=eff_nights_min,
            nights_max=eff_nights_max,
            max_price=max_price,
            dest_iata_list=resolved_iata_list if resolved_iata_list else None,
            max_stops=max_stops,
            max_layover_hours=max_layover_hours,
            max_travel_hours=max_travel_hours,
        )
        if trip_type == "roundtrip" and sources in ("all", "kiwi", "ryanair")
        else asyncio.sleep(0, result=[])
    )

    amadeus_on = is_amadeus_configured()
    tp_on = is_travelpayouts_configured()

    amadeus_task = (
        search_amadeus_flights(
            airports=clean_airports,
            date_from=date_from,
            date_to=date_to,
            trip_type=trip_type,
            nights_min=eff_nights_min,
            nights_max=eff_nights_max,
            max_price=max_price,
            dest_iata_list=resolved_iata_list if resolved_iata_list else None,
            max_stops=max_stops,
            max_layover_hours=max_layover_hours,
            max_travel_hours=max_travel_hours,
        )
        if amadeus_on and sources in ("all", "amadeus", "kiwi")
        else asyncio.sleep(0, result=[])
    )

    travelpayouts_task = (
        search_travelpayouts_flights(
            airports=clean_airports,
            date_from=date_from,
            date_to=date_to,
            trip_type=trip_type,
            nights_min=eff_nights_min,
            nights_max=eff_nights_max,
            max_price=max_price,
            dest_iata_list=resolved_iata_list if resolved_iata_list else None,
            max_stops=max_stops,
        )
        if tp_on and sources in ("all", "travelpayouts", "kiwi")
        else asyncio.sleep(0, result=[])
    )

    gathered = await asyncio.gather(
        ryanair_task, split_task, amadeus_task, travelpayouts_task, *kiwi_tasks, return_exceptions=True
    )
    ryanair_raw = gathered[0] if isinstance(gathered[0], list) else []
    split_raw = gathered[1] if isinstance(gathered[1], list) else []
    amadeus_raw = gathered[2] if isinstance(gathered[2], list) else []
    travelpayouts_raw = gathered[3] if isinstance(gathered[3], list) else []
    kiwi_raw: List[Dict[str, Any]] = []
    for item in gathered[4:]:
        if isinstance(item, list):
            kiwi_raw.extend(item)

    max_travel_sec = int(max_travel_hours) * 3600 if max_travel_hours else 999999
    filtered_ryanair = []
    for f in ryanair_raw:
        if f.get("outbound_duration_sec", 0) > max_travel_sec:
            continue
        if resolved_iata_list and f["dest_iata"] not in resolved_iata_list:
            continue
        filtered_ryanair.append(f)

    filtered_split = [
        f for f in split_raw
        if f.get("outbound_duration_sec", 0) <= max_travel_sec
        and (not resolved_iata_list or f["dest_iata"] in resolved_iata_list)
    ]

    filtered_amadeus = [
        f for f in amadeus_raw
        if f.get("outbound_duration_sec", 0) <= max_travel_sec
        and (not resolved_iata_list or f["dest_iata"] in resolved_iata_list)
    ]

    filtered_travelpayouts = [
        f for f in travelpayouts_raw
        if f.get("outbound_duration_sec", 0) <= max_travel_sec
        and (not resolved_iata_list or f["dest_iata"] in resolved_iata_list)
    ]

    combined_raw = (
        filtered_ryanair
        + filtered_split
        + filtered_amadeus
        + filtered_travelpayouts
        + list(kiwi_raw)
    )

    if dest_query and dest_query.strip() and not resolved_iata_list and not is_whole_world:
        tokens = [p.strip().lower() for p in dest_query.replace(";", ",").split(",") if p.strip()]
        combined_raw = [
            f for f in combined_raw
            if any(t in f"{f['dest_iata']} {f['dest_city']} {f['dest_name']} {f['dest_country']}".lower() for t in tokens)
        ]

    # 1. Cross-source exact flight comparison
    exact_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in combined_raw:
        in_d = (f.get("inbound_departure") or "")[:10]
        key = f"{f['origin_iata']}-{f['dest_iata']}-{f['outbound_departure'][:10]}-{in_d}-{f.get('stops_count', 0)}"
        exact_groups[key].append(f)

    deduped_flights: List[Dict[str, Any]] = []
    direct_vs_aggregator_matches: List[Dict[str, Any]] = []

    for key, group in exact_groups.items():
        group.sort(key=lambda x: x["price_pln"])
        winner = dict(group[0])
        other_offers = []
        for alt in group[1:]:
            other_offers.append({
                "source_label": alt["source_label"],
                "carrier_name": alt["carrier_name"],
                "price_pln": alt["price_pln"],
                "diff_pln": round(alt["price_pln"] - winner["price_pln"], 2),
                "booking_url": alt["primary_booking_url"],
            })

        sources_in_group = {g["source_engine"] for g in group}
        if len(sources_in_group) > 1:
            direct_offer = next(
                (g for g in group if g["source_engine"] == "ryanair_direct"),
                None,
            ) or next(
                (g for g in group if g["source_engine"] == "split_oneway"),
                None,
            )
            kw_offer = next(
                (g for g in group if g["source_engine"] in ("kiwi_multi", "amadeus_gds", "travelpayouts")),
                None,
            )
            if direct_offer and kw_offer:
                diff = round(abs(direct_offer["price_pln"] - kw_offer["price_pln"]), 2)
                cheaper_src = (
                    direct_offer["source_label"]
                    if direct_offer["price_pln"] <= kw_offer["price_pln"]
                    else kw_offer["source_label"]
                )
                direct_vs_aggregator_matches.append({
                    "route": f"{winner['origin_city']} ({winner['origin_iata']}) → {winner['dest_city']} ({winner['dest_iata']})",
                    "flag": winner["flag"],
                    "dates": (
                        f"{winner['outbound_departure'][:10]} ⇄ {winner['inbound_departure'][:10]}"
                        if winner.get("inbound_departure")
                        else winner["outbound_departure"][:10]
                    ),
                    "ryanair_direct_pln": direct_offer["price_pln"],
                    "aggregator_pln": kw_offer["price_pln"],
                    "aggregator_carrier": kw_offer["carrier_name"],
                    "cheaper_source": cheaper_src,
                    "savings_pln": diff,
                })

        winner["alternative_sources"] = other_offers
        deduped_flights.append(winner)

    # 2. Update & fetch historical route stats
    hist_stats = update_and_get_route_stats(deduped_flights)

    # 3. Enrich with Weather, Cost of Living, Baggage, Weekend Flag & Filter by weekend_only / warm_only
    group_size = max(1, int(group_size))
    post_filtered_flights: List[Dict[str, Any]] = []

    for f in deduped_flights:
        weather = get_destination_weather(f["dest_iata"], f.get("outbound_departure", ""))
        cost_info = get_destination_cost_info(f["dest_iata"], f.get("dest_country_code", ""))
        wknd = is_weekend_friendly(
            f.get("outbound_departure", ""), f.get("inbound_departure"), f.get("inbound_arrival")
        )
        comf_hrs = is_comfortable_hours(
            f.get("outbound_departure", ""),
            f.get("outbound_arrival", ""),
            f.get("inbound_departure"),
            f.get("inbound_arrival"),
        )

        if weekend_only and not wknd:
            continue
        if warm_only and not weather["is_warm"]:
            continue
        if comfortable_hours and not comf_hrs:
            continue

        base_price = float(f["price_pln"])
        bag_fee, bag_label = estimate_baggage_fee_pln(
            f.get("carrier_code", "FR"),
            f.get("stops_count", 0),
            f.get("trip_type", "roundtrip"),
            baggage_mode,
            group_size,
        )
        effective_price = round(base_price + bag_fee, 2)

        if max_price and effective_price > max_price:
            continue

        nights_stay = max(1, int(f.get("nights") or 3)) if f["trip_type"] == "roundtrip" else 0
        est_lodging_1p = nights_stay * cost_info["est_night_pln"]
        est_total_trip_1p = round(effective_price + est_lodging_1p, 2)

        # Visual timeline proportions (air vs layover)
        total_dur_sec = max(1800, (f.get("outbound_duration_sec") or 7200) + (f.get("inbound_duration_sec") or 0))
        total_lay_sec = max(0, f.get("total_layover_sec") or 0)
        lay_ratio = min(65, int(round((total_lay_sec / total_dur_sec) * 100))) if total_lay_sec > 0 else 0
        air_ratio = 100 - lay_ratio

        f["base_price_pln"] = base_price
        f["cabin_bag_fee_pln"] = bag_fee
        f["baggage_mode"] = baggage_mode
        f["baggage_label"] = bag_label
        f["price_pln"] = effective_price
        f["temp_c"] = weather["temp_c"]
        f["is_warm"] = weather["is_warm"]
        f["is_weekend_friendly"] = wknd
        f["is_comfortable_hours"] = comf_hrs
        f["cost_tier"] = cost_info["cost_tier"]
        f["cost_label"] = cost_info["cost_label"]
        f["est_night_pln"] = cost_info["est_night_pln"]
        f["est_lodging_per_person_pln"] = est_lodging_1p
        f["est_total_trip_per_person_pln"] = est_total_trip_1p
        f["est_total_trip_group_pln"] = round(est_total_trip_1p * group_size, 2)
        if f.get("trip_type") == "roundtrip" and f.get("inbound_departure"):
            f["lodging_links"] = build_lodging_links(
                f.get("dest_city", ""),
                f.get("outbound_departure", ""),
                f.get("inbound_departure", ""),
                group_size,
            )
        f["air_ratio_pct"] = air_ratio
        f["layover_ratio_pct"] = lay_ratio

        post_filtered_flights.append(f)

    # 4. Destination-level comparison
    dest_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in post_filtered_flights:
        dest_groups[f["dest_city"]].append(f)

    dest_avg_map: Dict[str, float] = {}
    destination_comparisons: List[Dict[str, Any]] = []

    for city, items in dest_groups.items():
        items.sort(key=lambda x: x["price_pln"])
        prices = [x["price_pln"] for x in items]
        avg_p = round(sum(prices) / len(prices), 2)
        dest_avg_map[city] = avg_p

        best = items[0]
        carriers_summary = {}
        for it in items:
            c_name = it["carrier_name"]
            if c_name not in carriers_summary or it["price_pln"] < carriers_summary[c_name]["price_pln"]:
                carriers_summary[c_name] = {
                    "carrier_name": c_name,
                    "origin_iata": it["origin_iata"],
                    "price_pln": it["price_pln"],
                    "stops_count": it.get("stops_count", 0),
                    "duration_str": it.get("outbound_duration_str", ""),
                    "dates": (
                        f"{it['outbound_departure'][:10]} ({it['nights']} dni)"
                        if it["trip_type"] == "roundtrip"
                        else it["outbound_departure"][:10]
                    ),
                    "booking_url": it["primary_booking_url"],
                }

        destination_comparisons.append({
            "dest_city": city,
            "dest_iata": best["dest_iata"],
            "flag": best["flag"],
            "temp_c": best.get("temp_c"),
            "cost_label": best.get("cost_label"),
            "best_price_pln": best["price_pln"],
            "avg_price_pln": avg_p,
            "max_price_pln": max(prices),
            "spread_pln": round(max(prices) - best["price_pln"], 2),
            "best_carrier": best["carrier_name"],
            "best_origin": f"{best['origin_city']} ({best['origin_iata']})",
            "offers_count": len(items),
            "carriers_breakdown": sorted(carriers_summary.values(), key=lambda x: x["price_pln"]),
        })

    destination_comparisons.sort(key=lambda x: x["best_price_pln"])

    # 5. Final scoring & historical comparison
    for f in post_filtered_flights:
        rk = f"{f['origin_iata']}-{f['dest_iata']}-{f['trip_type']}"
        h = hist_stats.get(rk, {})
        hist_avg = h.get("avg_price_pln", f["price_pln"])
        hist_min = h.get("min_price_pln", f["price_pln"])
        dest_avg = dest_avg_map.get(f["dest_city"], f["price_pln"])

        score, badge, time_score, time_badge = _compute_time_and_deal_scores(f, dest_avg, hist_avg)
        f["deal_score"] = score
        f["deal_badge"] = badge
        f["time_respect_score"] = time_score
        f["time_respect_badge"] = time_badge
        f["dest_avg_pln"] = dest_avg
        f["savings_vs_dest_avg_pln"] = round(max(0.0, dest_avg - f["price_pln"]), 2)
        f["historical_min_pln"] = hist_min
        f["historical_avg_pln"] = hist_avg
        f["is_historical_low"] = f["base_price_pln"] <= hist_min

        ref_avg = max(hist_avg, dest_avg)
        if ref_avg > f["price_pln"]:
            pct = int(round(((ref_avg - f["price_pln"]) / ref_avg) * 100))
            f["hist_savings_pct"] = pct
        else:
            f["hist_savings_pct"] = 0

        f["group_size"] = group_size
        f["group_total_pln"] = round(f["price_pln"] * group_size, 2)

        dist = f.get("distance_km")
        if dist:
            total_km = dist * (2 if f["trip_type"] == "roundtrip" else 1)
            f["pln_per_100km"] = round((f["price_pln"] / total_km) * 100.0, 2)
        else:
            f["pln_per_100km"] = None

    if sort_by == "smart":
        post_filtered_flights.sort(key=lambda x: (-x["deal_score"], x["price_pln"]))
    elif sort_by == "total_trip":
        post_filtered_flights.sort(key=lambda x: (x["est_total_trip_per_person_pln"], -x["deal_score"]))
    else:
        post_filtered_flights.sort(key=lambda x: (x["price_pln"], -x["time_respect_score"]))

    carrier_counts: Dict[str, Dict[str, Any]] = {}
    for f in post_filtered_flights:
        c = f["carrier_name"]
        if c not in carrier_counts:
            carrier_counts[c] = {"carrier": c, "count": 0, "min_price_pln": f["price_pln"]}
        carrier_counts[c]["count"] += 1
        carrier_counts[c]["min_price_pln"] = min(carrier_counts[c]["min_price_pln"], f["price_pln"])

    history_keys = [
        f"{f['origin_iata']}-{f['dest_iata']}-{f['trip_type']}"
        for f in post_filtered_flights[:50]
    ]
    route_history = get_route_history_summary(history_keys, limit=24)

    engines_active = [
        "Ryanair Direct API",
        "Kiwi Multi-Airline",
        "Składak 2× One-Way",
        "Czartery RSS (Fly4free + MlecznePodróże + WakacyjniPiraci)",
    ]
    if amadeus_on:
        engines_active.append("Amadeus GDS API")
    if tp_on:
        engines_active.append("Travelpayouts / Aviasales API")

    return {
        "meta": {
            "total_deals": len(post_filtered_flights),
            "ryanair_direct_count": len(filtered_ryanair),
            "split_oneway_count": len(filtered_split),
            "kiwi_multi_count": len(kiwi_raw),
            "amadeus_gds_count": len(filtered_amadeus),
            "travelpayouts_count": len(filtered_travelpayouts),
            "engines_count": len(engines_active),
            "engines_active": engines_active,
            "airports_scanned": clean_airports,
            "date_from": date_from,
            "date_to": date_to,
            "trip_type": trip_type,
            "group_size": group_size,
            "max_stops": max_stops,
            "max_layover_hours": max_layover_hours,
            "max_travel_hours": max_travel_hours,
            "weekend_only": weekend_only,
            "warm_only": warm_only,
            "comfortable_hours": comfortable_hours,
            "baggage_mode": baggage_mode,
            "cheapest_overall_pln": post_filtered_flights[0]["price_pln"] if post_filtered_flights else None,
            "carriers_summary": sorted(carrier_counts.values(), key=lambda x: x["min_price_pln"]),
        },
        "flights": post_filtered_flights,
        "destination_comparisons": destination_comparisons,
        "direct_vs_aggregator": direct_vs_aggregator_matches,
        "route_history": route_history,
    }
