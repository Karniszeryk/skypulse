import asyncio
import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Dict, Any, Optional
import httpx

FEED_SOURCES = [
    {"id": "fly4free", "name": "Fly4free.pl", "url": "https://www.fly4free.pl/feed/"},
    {"id": "mleczne", "name": "MlecznePodroze.pl", "url": "https://mlecznepodroze.pl/feed/"},
    {"id": "wakacyjnipiraci", "name": "WakacyjniPiraci.pl", "url": "https://www.wakacyjnipiraci.pl/feed"},
]

_CACHE_TTL_SEC = 900  # 15 minutes cache
_RSS_CACHE: Dict[str, Any] = {"ts": 0.0, "items": []}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PRICE_RE = re.compile(r"(\d[\d\s]{1,5})\s*(?:PLN|zł)", re.IGNORECASE)


def _strip_html(raw: str) -> str:
    txt = html.unescape(raw or "")
    txt = _TAG_RE.sub(" ", txt)
    txt = _WS_RE.sub(" ", txt).strip()
    return txt


def _norm_pl(text: str) -> str:
    t = (text or "").lower()
    for src, dst in [("ą", "a"), ("ć", "c"), ("ę", "e"), ("ł", "l"), ("ń", "n"), ("ó", "o"), ("ś", "s"), ("ź", "z"), ("ż", "z")]:
        t = t.replace(src, dst)
    return t


def _extract_price_pln(title: str, summary: str) -> Optional[int]:
    for text in (title, summary):
        m = _PRICE_RE.search(text or "")
        if m:
            digits = re.sub(r"\s+", "", m.group(1))
            try:
                val = int(digits)
                if 29 <= val <= 15000:
                    return val
            except Exception:
                pass
    return None


def _classify_deal(title: str, cats: List[str], price_pln: Optional[int], link: str = "") -> str:
    blob = _norm_pl(f"{title} {' '.join(cats)} {link}")
    if any(k in blob for k in ["czarter", "tui", "rainbow", "itaka", "coral", "wakacje", "all inclusive", "wczasy", "pakiety", "first minute", "last minute"]):
        return "czarter"
    if any(k in blob for k in ["blad", "error", "hit", "mega", "okazja"]):
        return "hit"
    if price_pln and price_pln <= 180:
        return "hit"
    return "loty"


async def _fetch_single_feed(client: httpx.AsyncClient, src: Dict[str, str]) -> List[Dict[str, Any]]:
    try:
        resp = await client.get(src["url"], timeout=10.0)
        if resp.status_code != 200 or not resp.text:
            return []
        root = ET.fromstring(resp.text.lstrip("\ufeff \t\r\n"))
    except Exception:
        return []

    items: List[Dict[str, Any]] = []
    for item_el in root.findall(".//item")[:25]:
        title = html.unescape((item_el.findtext("title") or "").strip())
        link = (item_el.findtext("link") or "").strip()
        desc = _strip_html(item_el.findtext("description") or "")
        pub_raw = (item_el.findtext("pubDate") or "").strip()
        cats = [html.unescape((c.text or "").strip()) for c in item_el.findall("category") if c.text]

        if not title or not link:
            continue

        ts = 0.0
        pub_label = pub_raw[:16]
        try:
            dt = parsedate_to_datetime(pub_raw)
            ts = dt.timestamp()
            pub_label = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            pass

        price_pln = _extract_price_pln(title, desc)
        kind = _classify_deal(title, cats, price_pln, link)
        kind_label = {
            "czarter": "Czarter / Pakiet",
            "hit": "Błąd / Hit cenowy",
            "loty": "Tanie loty",
        }[kind]

        short_desc = desc[:190].rstrip(" ,.;") + ("..." if len(desc) > 190 else "")
        items.append({
            "source_id": src["id"],
            "source_name": src["name"],
            "title": title,
            "link": link,
            "summary": short_desc,
            "categories": cats[:4],
            "deal_kind": kind,
            "deal_kind_label": kind_label,
            "price_pln": price_pln,
            "published_ts": ts,
            "published_label": pub_label,
        })
    return items


async def fetch_promo_rss_deals(query: str = "", category: str = "all") -> Dict[str, Any]:
    now_ts = time.time()
    if not _RSS_CACHE["items"] or (now_ts - _RSS_CACHE["ts"]) > _CACHE_TTL_SEC:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
            follow_redirects=True,
        ) as client:
            batches = await asyncio.gather(
                *[_fetch_single_feed(client, src) for src in FEED_SOURCES],
                return_exceptions=True,
            )
        merged: List[Dict[str, Any]] = []
        seen_links = set()
        for b in batches:
            if isinstance(b, list):
                for item in b:
                    if item["link"] not in seen_links:
                        seen_links.add(item["link"])
                        merged.append(item)
        merged.sort(key=lambda x: x["published_ts"], reverse=True)
        if merged:
            _RSS_CACHE["ts"] = now_ts
            _RSS_CACHE["items"] = merged

    all_items: List[Dict[str, Any]] = _RSS_CACHE["items"]
    cat_clean = (category or "all").strip().lower()
    q_clean = _norm_pl(query).strip()

    filtered = []
    for it in all_items:
        if cat_clean in ("czarter", "hit", "loty") and it["deal_kind"] != cat_clean:
            continue
        if cat_clean in ("fly4free", "mleczne", "wakacyjnipiraci") and it["source_id"] != cat_clean:
            continue
        if q_clean:
            hay = _norm_pl(f"{it['title']} {it['summary']} {' '.join(it['categories'])}")
            if q_clean not in hay:
                continue
        filtered.append(it)

    return {
        "total": len(filtered),
        "cached_total": len(all_items),
        "updated_label": datetime.fromtimestamp(_RSS_CACHE["ts"]).strftime("%H:%M:%S") if _RSS_CACHE["ts"] else "",
        "sources": [s["name"] for s in FEED_SOURCES],
        "items": filtered[:75],
    }
