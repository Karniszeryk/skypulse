from datetime import date, timedelta
from typing import List, Dict, Any, Optional


def easter_sunday(year: int) -> date:
    """Computes Gregorian Easter Sunday for any year using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _expand_to_long_weekend(anchor: date, extra_days_before: int = 1, extra_days_after: int = 1) -> tuple[date, date]:
    """Expands a fixed/single-day holiday to the practical surrounding weekend travel window."""
    wd = anchor.weekday()  # Mon=0 .. Sun=6
    if wd == 0:  # Monday
        return anchor - timedelta(days=3), anchor + timedelta(days=1)
    if wd == 1:  # Tuesday -> take Fri/Sat..Tue+1
        return anchor - timedelta(days=4), anchor + timedelta(days=1)
    if wd == 2:  # Wednesday -> Wed-1 .. Sun
        return anchor - timedelta(days=1), anchor + timedelta(days=4)
    if wd == 3:  # Thursday -> Wed..Sun/Mon
        return anchor - timedelta(days=1), anchor + timedelta(days=4)
    if wd == 4:  # Friday -> Thu/Fri..Mon
        return anchor - timedelta(days=extra_days_before), anchor + timedelta(days=3)
    if wd == 5:  # Saturday -> Fri..Mon
        return anchor - timedelta(days=1), anchor + timedelta(days=2)
    # Sunday -> Fri..Mon
    return anchor - timedelta(days=2), anchor + timedelta(days=1)


def _format_short_range(d1: date, d2: date) -> str:
    if d1.month == d2.month:
        return f"{d1.day}–{d2.day}.{d2.month:02d}"
    return f"{d1.day}.{d1.month:02d}–{d2.day}.{d2.month:02d}"


def _build_year_holidays(year: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    # 1. Ferie Zimowe (Poland rotating winter break mid-Jan to end-Feb)
    ferie_s = date(year, 1, 16)
    ferie_e = date(year, 2, 28)
    items.append({
        "id": "ferie",
        "label": f"Ferie Zimowe {year}",
        "title": "Ferie Zimowe",
        "date_from": ferie_s.isoformat(),
        "date_to": ferie_e.isoformat(),
        "nights_min": 4,
        "nights_max": 8,
    })

    # 2. Wielkanoc (Easter Sunday - 2 days to + 3 days)
    easter = easter_sunday(year)
    w_s = easter - timedelta(days=2)
    w_e = easter + timedelta(days=3)
    items.append({
        "id": "wielkanoc",
        "label": f"Wielkanoc ({_format_short_range(w_s, w_e)})",
        "title": "Wielkanoc",
        "date_from": w_s.isoformat(),
        "date_to": w_e.isoformat(),
        "nights_min": 3,
        "nights_max": 5,
    })

    # 3. Majówka (1-3 May extended to adjacent weekend)
    may1 = date(year, 5, 1)
    may3 = date(year, 5, 3)
    m_s = may1 - timedelta(days=(1 if may1.weekday() == 5 else (2 if may1.weekday() == 6 else (3 if may1.weekday() == 0 else (4 if may1.weekday() == 1 else 1)))))
    m_e = may3 + timedelta(days=(1 if may3.weekday() in (5, 6, 0) else (4 - may3.weekday() if may3.weekday() < 4 else 2)))
    if (m_e - m_s).days < 3:
        m_e = m_s + timedelta(days=4)
    items.append({
        "id": "majowka",
        "label": f"Majówka ({_format_short_range(m_s, m_e)})",
        "title": "Majówka",
        "date_from": m_s.isoformat(),
        "date_to": m_e.isoformat(),
        "nights_min": 3,
        "nights_max": 5,
    })

    # 4. Boże Ciało (always Thursday = Easter + 60 days)
    corpus = easter + timedelta(days=60)
    cc_s = corpus - timedelta(days=1)
    cc_e = corpus + timedelta(days=4)
    items.append({
        "id": "bozecialo",
        "label": f"Boże Ciało ({_format_short_range(cc_s, cc_e)})",
        "title": "Boże Ciało",
        "date_from": cc_s.isoformat(),
        "date_to": cc_e.isoformat(),
        "nights_min": 3,
        "nights_max": 5,
    })

    # 5. Długi weekend sierpniowy (15 August)
    aug_s, aug_e = _expand_to_long_weekend(date(year, 8, 15))
    items.append({
        "id": "sierpien",
        "label": f"15 Sierpnia ({_format_short_range(aug_s, aug_e)})",
        "title": "Długi weekend sierpniowy",
        "date_from": aug_s.isoformat(),
        "date_to": aug_e.isoformat(),
        "nights_min": 3,
        "nights_max": 5,
    })

    # 6. Święto Niepodległości (11 November)
    nov_s, nov_e = _expand_to_long_weekend(date(year, 11, 11))
    items.append({
        "id": "listopad",
        "label": f"11 Listopada ({_format_short_range(nov_s, nov_e)})",
        "title": "Długi weekend listopadowy",
        "date_from": nov_s.isoformat(),
        "date_to": nov_e.isoformat(),
        "nights_min": 3,
        "nights_max": 5,
    })

    # 7. Mikołajki / Jarmarki grudniowe (6 December)
    mik_s, mik_e = _expand_to_long_weekend(date(year, 12, 6))
    items.append({
        "id": "mikolajki",
        "label": f"Mikołajki ({_format_short_range(mik_s, mik_e)})",
        "title": "Mikołajki / Jarmarki",
        "date_from": mik_s.isoformat(),
        "date_to": mik_e.isoformat(),
        "nights_min": 2,
        "nights_max": 4,
    })

    # 8. Święta & Sylwester (Dec 23 .. Jan 3)
    ny_s = date(year, 12, 23)
    ny_e = date(year + 1, 1, 3)
    items.append({
        "id": "sylwester",
        "label": f"Święta / Sylwester {year}/{str(year + 1)[2:]}",
        "title": "Święta i Sylwester",
        "date_from": ny_s.isoformat(),
        "date_to": ny_e.isoformat(),
        "nights_min": 3,
        "nights_max": 7,
    })

    return items


def get_upcoming_polish_holidays(ref_date: Optional[date] = None, limit: int = 6) -> List[Dict[str, Any]]:
    """Returns the next `limit` upcoming Polish holidays/long weekends starting from `ref_date`."""
    today = ref_date or date.today()
    candidates = _build_year_holidays(today.year - 1) + _build_year_holidays(today.year) + _build_year_holidays(today.year + 1)

    upcoming: List[Dict[str, Any]] = []
    seen_ids = set()
    # Keep chronological order
    candidates.sort(key=lambda x: x["date_from"])

    for item in candidates:
        end_dt = date.fromisoformat(item["date_to"])
        start_dt = date.fromisoformat(item["date_from"])
        if end_dt < today + timedelta(days=2):
            continue
        if item["id"] in seen_ids:
            continue
        seen_ids.add(item["id"])
        # Clamp start date if we're already inside an ongoing season like Ferie
        eff_start = max(start_dt, today + timedelta(days=2))
        if (end_dt - eff_start).days < 2:
            continue
        upcoming.append({
            **item,
            "date_from": eff_start.isoformat(),
        })
        if len(upcoming) >= limit:
            break

    return upcoming
