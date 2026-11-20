import argparse
import asyncio
import sys
from datetime import datetime, timedelta

from backend.database import init_db
from backend.comparator import scan_and_compare_flights
from backend.notifier import send_deals_notification

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser(description="TanieLoty CLI – Skrypt porównujący najtańsze loty")
    parser.add_argument("--airports", default="KRK,WAW,WMI,KTW,GDN,WRO,POZ", help="Kody lotnisk wylotu po przecinku")
    parser.add_argument("--dest", default="", help="Kierunek (kod IATA, miasto lub kraj, np. BGY, Włochy)")
    parser.add_argument("--type", choices=["roundtrip", "oneway"], default="roundtrip", help="Typ lotu")
    parser.add_argument("--days-ahead", type=int, default=35, help="Zakres dni do przodu")
    parser.add_argument("--nights-min", type=int, default=2, help="Min. liczba nocy (dla roundtrip)")
    parser.add_argument("--nights-max", type=int, default=5, help="Max. liczba nocy (dla roundtrip)")
    parser.add_argument("--max-price", type=int, default=350, help="Maksymalna cena w PLN za osobę")
    parser.add_argument("--max-stops", type=int, default=1, help="Maksymalna liczba przesiadek (0, 1 lub 2)")
    parser.add_argument("--max-layover", type=int, default=5, help="Maksymalny czas pojedynczej przesiadki w godz.")
    parser.add_argument("--max-travel", type=int, default=14, help="Maksymalny łączny czas podróży w godz.")
    parser.add_argument("--group", type=int, default=4, help="Liczba osób (Ty + znajomi)")
    parser.add_argument("--limit", type=int, default=15, help="Ile najtańszych wyników wypisać")
    parser.add_argument("--notify", action="store_true", help="Wyślij najtańsze loty na Discord / Telegram")

    args = parser.parse_args()
    init_db()

    today = datetime.now().date()
    date_from = (today + timedelta(days=2)).isoformat()
    date_to = (today + timedelta(days=args.days_ahead)).isoformat()
    airports = [a.strip().upper() for a in args.airports.split(",") if a.strip()]

    print("=" * 88)
    print(f"✈️  TanieLoty Scanner | Lotniska: {', '.join(airports)} | Zakres: {date_from} -> {date_to}")
    print(f"👥 Ekipa: {args.group} os. | Max cena: {args.max_price} PLN/os. | Tryb: {args.type.upper()}")
    print("=" * 88)

    result = await scan_and_compare_flights(
        airports=airports,
        date_from=date_from,
        date_to=date_to,
        trip_type=args.type,
        nights_min=args.nights_min,
        nights_max=args.nights_max,
        max_price=args.max_price,
        dest_query=args.dest,
        group_size=args.group,
        max_stops=args.max_stops,
        max_layover_hours=args.max_layover,
        max_travel_hours=args.max_travel,
    )

    meta = result["meta"]
    flights = result["flights"]
    print(
        f"Pobrano ofert -> Ryanair Direct API: {meta['ryanair_direct_count']} | "
        f"Multi-Airline (WizzAir/Norwegian/LOT/Kiwi): {meta['kiwi_multi_count']} | "
        f"Po porównaniu i deduplikacji: {meta['total_deals']}\n"
    )

    if not flights:
        print("Brak lotów spełniających kryteria. Spróbuj zwiększyć --max-price lub zakres dni.")
        return

    print(f"{'#':<3} {'Ocena':<16} {'Trasa':<32} {'Linia / Źródło':<22} {'Termin':<23} {'1 os.':<10} {'Ekipa'}")
    print("-" * 118)
    for i, f in enumerate(flights[: args.limit], 1):
        route = f"{f['origin_iata']} -> {f['dest_city']} ({f['dest_iata']})"
        dates = (
            f"{f['outbound_departure'][:10]} ({f['nights']} n.)"
            if f["trip_type"] == "roundtrip"
            else f["outbound_departure"][:10]
        )
        price_1 = f"{f['price_pln']:.0f} PLN"
        price_grp = f"{f['group_total_pln']:.0f} PLN ({args.group} os.)"
        print(
            f"{i:<3} {f['deal_badge']:<16} {route:<32} {f['carrier_name'][:20]:<22} {dates:<23} {price_1:<10} {price_grp}"
        )

    if result["destination_comparisons"]:
        print("\n📊 PORÓWNANIE KIERUNKÓW (Najtańszy vs Średnia cena na trasie):")
        for dc in result["destination_comparisons"][:6]:
            print(
                f" • {dc['flag']} {dc['dest_city']} ({dc['dest_iata']}): najtaniej {dc['best_price_pln']:.0f} PLN "
                f"({dc['best_carrier']} z {dc['best_origin']}) vs średnia {dc['avg_price_pln']:.0f} PLN "
                f"[rozrzut cen: {dc['spread_pln']:.0f} PLN]"
            )

    if args.notify:
        notif = await send_deals_notification(flights, group_size=args.group)
        print("\n🔔 Status powiadomienia:", notif)


if __name__ == "__main__":
    asyncio.run(main())
