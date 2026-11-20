import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from backend.security import (
    encrypt_secret,
    decrypt_secret,
    mask_secret,
    hash_access_code,
    verify_access_code,
)

DB_PATH = Path(__file__).resolve().parent.parent / "tanie_loty.db"

ENCRYPTED_KEYS = {
    "discord_webhook_url",
    "telegram_bot_token",
    "telegram_chat_id",
    "whatsapp_apikey",
    "smtp_password",
    "amadeus_client_secret",
    "travelpayouts_token",
}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS route_stats (
        route_key TEXT PRIMARY KEY,
        origin_iata TEXT NOT NULL,
        dest_iata TEXT NOT NULL,
        dest_city TEXT,
        trip_type TEXT NOT NULL,
        min_price_pln REAL NOT NULL,
        max_price_pln REAL NOT NULL,
        avg_price_pln REAL NOT NULL,
        samples_count INTEGER NOT NULL DEFAULT 1,
        last_price_pln REAL NOT NULL,
        last_carrier TEXT,
        updated_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        airports TEXT NOT NULL,
        dest_query TEXT DEFAULT '',
        trip_type TEXT DEFAULT 'roundtrip',
        max_price_pln REAL NOT NULL,
        group_size INTEGER DEFAULT 1,
        nights_min INTEGER DEFAULT 2,
        nights_max INTEGER DEFAULT 5,
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS saved_deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deal_id TEXT UNIQUE NOT NULL,
        origin_iata TEXT NOT NULL,
        origin_city TEXT,
        dest_iata TEXT NOT NULL,
        dest_city TEXT,
        flag TEXT,
        carrier_name TEXT,
        trip_type TEXT,
        outbound_departure TEXT,
        inbound_departure TEXT,
        nights INTEGER,
        price_pln REAL NOT NULL,
        group_size INTEGER DEFAULT 1,
        note TEXT DEFAULT '',
        votes INTEGER DEFAULT 1,
        voters_json TEXT DEFAULT '[]',
        booking_url TEXT,
        raw_json TEXT,
        saved_at TEXT NOT NULL
    )
    """)

    # Add anti-spam notification tracking columns to watchlist if upgrading existing table
    for col_name, col_def in [
        ("last_notified_price", "REAL DEFAULT NULL"),
        ("last_notified_route", "TEXT DEFAULT ''"),
        ("last_notified_at", "TEXT DEFAULT NULL"),
        ("last_checked_at", "TEXT DEFAULT NULL"),
    ]:
        try:
            cur.execute(f"ALTER TABLE watchlist ADD COLUMN {col_name} {col_def}")
        except Exception:
            pass

    # Add voting tracking columns to saved_deals if upgrading existing table
    for col_name, col_def in [
        ("voters_json", "TEXT DEFAULT '[]'"),
        ("votes_map_json", "TEXT DEFAULT '{}'"),
        ("upvotes", "INTEGER DEFAULT 0"),
        ("downvotes", "INTEGER DEFAULT 0"),
    ]:
        try:
            cur.execute(f"ALTER TABLE saved_deals ADD COLUMN {col_name} {col_def}")
        except Exception:
            pass

    # Backfill legacy saved_deals rows where votes_map_json is still empty
    try:
        cur.execute("SELECT id, voters_json, votes_map_json, votes FROM saved_deals")
        for srow in cur.fetchall():
            vmap_raw = (srow["votes_map_json"] or "{}").strip()
            if vmap_raw in ("", "{}"):
                try:
                    legacy_v = json.loads(srow["voters_json"] or "[]")
                except Exception:
                    legacy_v = []
                backfill = {}
                for lnick in legacy_v:
                    cn = str(lnick).strip()[:25]
                    if cn:
                        lkey = f"nick:{cn.lower()}"
                        backfill[lkey] = {"nick": cn, "vote": 1}
                up_cnt = len(backfill)
                if up_cnt > 0:
                    cur.execute(
                        "UPDATE saved_deals SET votes_map_json=?, upvotes=?, downvotes=0, votes=? WHERE id=?",
                        (json.dumps(backfill, ensure_ascii=False), up_cnt, up_cnt, srow["id"]),
                    )
    except Exception:
        pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    # Seed default watchlist items if empty
    cur.execute("SELECT COUNT(*) as cnt FROM watchlist")
    if cur.fetchone()["cnt"] == 0:
        now = datetime.now().isoformat(timespec="seconds")
        cur.execute(
            """INSERT INTO watchlist (title, airports, dest_query, trip_type, max_price_pln, group_size, nights_min, nights_max, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("Weekend ze znajomymi (Gdziekolwiek)", "KRK,KTW,WMI,WAW,GDN", "", "roundtrip", 200.0, 4, 2, 4, now),
        )
        cur.execute(
            """INSERT INTO watchlist (title, airports, dest_query, trip_type, max_price_pln, group_size, nights_min, nights_max, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("Tanie Włochy / Hiszpania", "KRK,KTW,WRO,POZ,WMI", "BGY,CIA,BRI,NAP,BCN,ALC,AGP", "roundtrip", 250.0, 2, 3, 6, now),
        )

    # Seed default security settings if not present
    cur.execute("SELECT value FROM settings WHERE key='squad_pin_hash'")
    if not cur.fetchone():
        default_hash = hash_access_code("loty2026")
        cur.execute("INSERT INTO settings (key, value) VALUES ('squad_pin_hash', ?)", (default_hash,))
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('require_pin', 'false')")
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('brand_name', 'SkyPulse.pl')")

    conn.commit()
    conn.close()


def update_and_get_route_stats(flights: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")

    cur.execute("SELECT * FROM route_stats")
    existing = {row["route_key"]: dict(row) for row in cur.fetchall()}

    best_in_scan: Dict[str, Dict[str, Any]] = {}
    for f in flights:
        rk = f"{f['origin_iata']}-{f['dest_iata']}-{f['trip_type']}"
        if rk not in best_in_scan or f["price_pln"] < best_in_scan[rk]["price_pln"]:
            best_in_scan[rk] = f

    for rk, f in best_in_scan.items():
        price = float(f["price_pln"])
        if rk in existing:
            ex = existing[rk]
            cnt = ex["samples_count"] + 1
            new_min = min(ex["min_price_pln"], price)
            new_max = max(ex["max_price_pln"], price)
            new_avg = round(((ex["avg_price_pln"] * ex["samples_count"]) + price) / cnt, 2)
            cur.execute(
                """UPDATE route_stats
                   SET min_price_pln=?, max_price_pln=?, avg_price_pln=?, samples_count=?,
                       last_price_pln=?, last_carrier=?, updated_at=?
                   WHERE route_key=?""",
                (new_min, new_max, new_avg, cnt, price, f["carrier_name"], now, rk),
            )
            existing[rk].update({
                "min_price_pln": new_min,
                "max_price_pln": new_max,
                "avg_price_pln": new_avg,
                "samples_count": cnt,
                "last_price_pln": price,
                "last_carrier": f["carrier_name"],
            })
        else:
            cur.execute(
                """INSERT INTO route_stats
                   (route_key, origin_iata, dest_iata, dest_city, trip_type,
                    min_price_pln, max_price_pln, avg_price_pln, samples_count,
                    last_price_pln, last_carrier, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    rk,
                    f["origin_iata"],
                    f["dest_iata"],
                    f["dest_city"],
                    f["trip_type"],
                    price,
                    price,
                    price,
                    price,
                    f["carrier_name"],
                    now,
                ),
            )
            existing[rk] = {
                "route_key": rk,
                "min_price_pln": price,
                "max_price_pln": price,
                "avg_price_pln": price,
                "samples_count": 1,
                "last_price_pln": price,
                "last_carrier": f["carrier_name"],
            }

    conn.commit()
    conn.close()
    return existing


def list_watchlist() -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM watchlist ORDER BY id DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_watchlist_item(item: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    airports = str(item.get("airports", "KRK,WAW,WMI,GDN,KTW"))[:80].strip()
    dest_query = str(item.get("dest_query", ""))[:80].strip()
    trip_type = str(item.get("trip_type", "roundtrip")).strip()
    max_price = float(item.get("max_price_pln", 200))
    group_size = int(item.get("group_size", 2))
    nights_min = int(item.get("nights_min", 2))
    nights_max = int(item.get("nights_max", 5))

    cur.execute(
        "SELECT id FROM watchlist WHERE airports=? AND dest_query=? AND trip_type=?",
        (airports, dest_query, trip_type),
    )
    existing = cur.fetchone()
    if existing:
        row_id = existing["id"]
        cur.execute(
            """UPDATE watchlist
               SET title=?, max_price_pln=?, group_size=?, nights_min=?, nights_max=?
               WHERE id=?""",
            (str(item.get("title", "Alert cenowy"))[:80], max_price, group_size, nights_min, nights_max, row_id),
        )
    else:
        cur.execute(
            """INSERT INTO watchlist (title, airports, dest_query, trip_type, max_price_pln, group_size, nights_min, nights_max, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(item.get("title", "Alert cenowy"))[:80],
                airports,
                dest_query,
                trip_type,
                max_price,
                group_size,
                nights_min,
                nights_max,
                now,
            ),
        )
        row_id = cur.lastrowid

    conn.commit()
    cur.execute("SELECT * FROM watchlist WHERE id=?", (row_id,))
    row = dict(cur.fetchone())
    conn.close()
    return row


def mark_watchlist_checked(
    item_id: int,
    notified_price: Optional[float] = None,
    notified_route: str = "",
) -> None:
    conn = get_conn()
    now = datetime.now().isoformat(timespec="seconds")
    if notified_price is not None:
        conn.execute(
            """UPDATE watchlist
               SET last_checked_at=?, last_notified_price=?, last_notified_route=?, last_notified_at=?
               WHERE id=?""",
            (now, float(notified_price), notified_route[:120], now, item_id),
        )
    else:
        conn.execute("UPDATE watchlist SET last_checked_at=? WHERE id=?", (now, item_id))
    conn.commit()
    conn.close()


def get_route_history_summary(route_keys: Optional[List[str]] = None, limit: int = 24) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    if route_keys:
        clean_keys = list(dict.fromkeys([k for k in route_keys if k]))[:60]
        if clean_keys:
            placeholders = ",".join("?" for _ in clean_keys)
            cur.execute(
                f"SELECT * FROM route_stats WHERE route_key IN ({placeholders}) ORDER BY last_price_pln ASC LIMIT ?",
                (*clean_keys, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
        else:
            rows = []
    else:
        cur.execute("SELECT * FROM route_stats ORDER BY samples_count DESC, min_price_pln ASC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    enriched: List[Dict[str, Any]] = []
    for r in rows:
        mn = float(r.get("min_price_pln") or 0)
        mx = float(r.get("max_price_pln") or mn)
        avg = float(r.get("avg_price_pln") or mn)
        cur_p = float(r.get("last_price_pln") or mn)

        spread = max(1.0, mx - mn)
        bar_pct = int(round(max(0.0, min(100.0, ((cur_p - mn) / spread) * 100.0)))) if mx > mn else 15
        diff_avg_pct = int(round(((cur_p - avg) / avg) * 100.0)) if avg > 0 else 0

        if cur_p <= mn * 1.03:
            trend_label = "Rekordowo tanio"
            trend_tone = "best"
        elif diff_avg_pct <= -6:
            trend_label = f"Taniej o {abs(diff_avg_pct)}%"
            trend_tone = "good"
        elif diff_avg_pct >= 8:
            trend_label = f"Drożej o {diff_avg_pct}%"
            trend_tone = "high"
        else:
            trend_label = "Cena stabilna"
            trend_tone = "neutral"

        enriched.append({
            **r,
            "bar_pct": bar_pct,
            "diff_from_avg_pct": diff_avg_pct,
            "trend_label": trend_label,
            "trend_tone": trend_tone,
        })
    return enriched


def delete_watchlist_item(item_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM watchlist WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


def _normalize_voter_key(voter_id: str, clean_nick: str) -> str:
    vid = (voter_id or "").strip()[:64]
    if vid:
        return f"id:{vid}"
    if clean_nick:
        return f"nick:{clean_nick.lower()}"
    return "anon"


def _compute_vote_summary(votes_map: Dict[str, Any]) -> Dict[str, Any]:
    up_nicks: List[str] = []
    down_nicks: List[str] = []
    for entry in votes_map.values():
        if not isinstance(entry, dict):
            continue
        v = int(entry.get("vote") or 0)
        nick = (entry.get("nick") or "Znajomy").strip()[:25]
        if v == 1:
            up_nicks.append(nick)
        elif v == -1:
            down_nicks.append(nick)
    up_cnt = len(up_nicks)
    down_cnt = len(down_nicks)
    net_score = up_cnt - down_cnt
    return {
        "upvotes": up_cnt,
        "downvotes": down_cnt,
        "net_votes": net_score,
        "up_nicks": up_nicks,
        "down_nicks": down_nicks,
    }


def list_saved_deals(voter_id: str = "") -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM saved_deals ORDER BY votes DESC, upvotes DESC, price_pln ASC")
    vid_key = f"id:{voter_id.strip()[:64]}" if voter_id and voter_id.strip() else ""
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        if d.get("raw_json"):
            try:
                d["details"] = json.loads(d["raw_json"])
            except Exception:
                d["details"] = {}
        try:
            vmap = json.loads(d.get("votes_map_json") or "{}")
            if not isinstance(vmap, dict):
                vmap = {}
        except Exception:
            vmap = {}
        summary = _compute_vote_summary(vmap)
        d["upvotes"] = summary["upvotes"]
        d["downvotes"] = summary["downvotes"]
        d["votes"] = summary["net_votes"]
        d["voters"] = summary["up_nicks"]
        d["downvoters"] = summary["down_nicks"]
        d["my_vote"] = int((vmap.get(vid_key) or {}).get("vote") or 0) if vid_key else 0
        d.pop("votes_map_json", None)
        rows.append(d)
    conn.close()
    return rows


def save_deal_for_group(
    deal: Dict[str, Any],
    group_size: int = 4,
    note: str = "",
    voter_nick: str = "Ja",
    voter_id: str = "",
) -> Dict[str, Any]:
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    deal_id = deal.get("id") or f"{deal.get('origin_iata')}-{deal.get('dest_iata')}-{deal.get('outbound_departure')}"
    clean_nick = (voter_nick or "Znajomy").strip()[:25]
    v_key = _normalize_voter_key(voter_id, clean_nick)

    cur.execute("SELECT id, votes_map_json FROM saved_deals WHERE deal_id=?", (deal_id,))
    existing = cur.fetchone()
    already_saved = False
    if existing:
        already_saved = True
        try:
            vmap = json.loads(existing["votes_map_json"] or "{}")
            if not isinstance(vmap, dict):
                vmap = {}
        except Exception:
            vmap = {}
        if v_key not in vmap:
            vmap[v_key] = {"nick": clean_nick, "vote": 1}
        summary = _compute_vote_summary(vmap)
        cur.execute(
            """UPDATE saved_deals
               SET votes=?, upvotes=?, downvotes=?, voters_json=?, votes_map_json=?
               WHERE id=?""",
            (
                summary["net_votes"],
                summary["upvotes"],
                summary["downvotes"],
                json.dumps(summary["up_nicks"], ensure_ascii=False),
                json.dumps(vmap, ensure_ascii=False),
                existing["id"],
            ),
        )
    else:
        vmap = {v_key: {"nick": clean_nick, "vote": 1}}
        summary = _compute_vote_summary(vmap)
        cur.execute(
            """INSERT INTO saved_deals
               (deal_id, origin_iata, origin_city, dest_iata, dest_city, flag, carrier_name,
                trip_type, outbound_departure, inbound_departure, nights, price_pln,
                group_size, note, votes, upvotes, downvotes, voters_json, votes_map_json, booking_url, raw_json, saved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                deal_id,
                deal.get("origin_iata", ""),
                deal.get("origin_city", ""),
                deal.get("dest_iata", ""),
                deal.get("dest_city", ""),
                deal.get("flag", "✈️"),
                deal.get("carrier_name", ""),
                deal.get("trip_type", "roundtrip"),
                deal.get("outbound_departure", ""),
                deal.get("inbound_departure", ""),
                int(deal.get("nights") or 0),
                float(deal.get("price_pln") or 0),
                int(group_size),
                note[:120],
                summary["net_votes"],
                summary["upvotes"],
                summary["downvotes"],
                json.dumps(summary["up_nicks"], ensure_ascii=False),
                json.dumps(vmap, ensure_ascii=False),
                deal.get("primary_booking_url", ""),
                json.dumps(deal, ensure_ascii=False),
                now,
            ),
        )
    conn.commit()
    cur.execute("SELECT * FROM saved_deals WHERE deal_id=?", (deal_id,))
    row = dict(cur.fetchone())
    row["already_saved"] = already_saved
    conn.close()
    return row


def vote_saved_deal(
    deal_db_id: int,
    delta: int = 1,
    voter_nick: str = "",
    voter_id: str = "",
) -> Dict[str, Any]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT votes_map_json FROM saved_deals WHERE id=?", (deal_db_id,))
    row = cur.fetchone()
    action = "none"
    my_vote = 0
    if row:
        try:
            vmap = json.loads(row["votes_map_json"] or "{}")
            if not isinstance(vmap, dict):
                vmap = {}
        except Exception:
            vmap = {}
        clean_nick = (voter_nick or "Znajomy").strip()[:25]
        v_key = _normalize_voter_key(voter_id, clean_nick)
        desired = 1 if int(delta) > 0 else -1
        current = int((vmap.get(v_key) or {}).get("vote") or 0)

        if current == desired:
            # Clicking the same thumb again cancels the vote (toggle off)
            vmap.pop(v_key, None)
            action = "removed"
            my_vote = 0
        else:
            vmap[v_key] = {"nick": clean_nick, "vote": desired}
            action = "up" if desired == 1 else "down"
            my_vote = desired

        summary = _compute_vote_summary(vmap)
        cur.execute(
            """UPDATE saved_deals
               SET votes=?, upvotes=?, downvotes=?, voters_json=?, votes_map_json=?
               WHERE id=?""",
            (
                summary["net_votes"],
                summary["upvotes"],
                summary["downvotes"],
                json.dumps(summary["up_nicks"], ensure_ascii=False),
                json.dumps(vmap, ensure_ascii=False),
                deal_db_id,
            ),
        )
        conn.commit()
    conn.close()
    return {"action": action, "my_vote": my_vote}


def delete_saved_deal(deal_db_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM saved_deals WHERE id=?", (deal_db_id,))
    conn.commit()
    conn.close()


def get_settings() -> Dict[str, str]:
    """Returns decrypted settings for internal backend use (e.g. notifier)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings")
    res = {}
    for r in cur.fetchall():
        k, v = r["key"], r["value"]
        if k in ENCRYPTED_KEYS:
            res[k] = decrypt_secret(v)
        else:
            res[k] = v
    conn.close()
    return res


def get_public_settings() -> Dict[str, Any]:
    """Returns masked settings safe to expose to the frontend browser."""
    raw = get_settings()
    return {
        "brand_name": raw.get("brand_name", "SkyPulse.pl"),
        "require_pin": raw.get("require_pin", "false") == "true",
        "discord_configured": bool(raw.get("discord_webhook_url")),
        "discord_masked": mask_secret(raw.get("discord_webhook_url", "")),
        "telegram_configured": bool(raw.get("telegram_bot_token") and raw.get("telegram_chat_id")),
        "telegram_token_masked": mask_secret(raw.get("telegram_bot_token", "")),
        "telegram_chat_id": raw.get("telegram_chat_id", ""),
        "whatsapp_phone": raw.get("whatsapp_phone", ""),
        "whatsapp_apikey_masked": mask_secret(raw.get("whatsapp_apikey", "")),
        "whatsapp_configured": bool(raw.get("whatsapp_phone")),
        "email_recipient": raw.get("email_recipient", ""),
        "smtp_sender": raw.get("smtp_sender", ""),
        "smtp_password_masked": mask_secret(raw.get("smtp_password", "")),
        "email_configured": bool(raw.get("email_recipient")),
        "amadeus_client_id": raw.get("amadeus_client_id", ""),
        "amadeus_secret_masked": mask_secret(raw.get("amadeus_client_secret", "")),
        "amadeus_env": raw.get("amadeus_env", "test") or "test",
        "amadeus_configured": bool(raw.get("amadeus_client_id") and raw.get("amadeus_client_secret")),
        "travelpayouts_token_masked": mask_secret(raw.get("travelpayouts_token", "")),
        "travelpayouts_marker": raw.get("travelpayouts_marker", ""),
        "travelpayouts_configured": bool(raw.get("travelpayouts_token")),
    }


def save_settings(new_settings: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_conn()
    cur = conn.cursor()

    for k, v in new_settings.items():
        if v is None:
            continue
        val_str = str(v).strip()
        # Ignore masked placeholders so user doesn't overwrite real secret with dots
        if "••••" in val_str:
            continue
        if k == "new_squad_pin" and val_str:
            hashed = hash_access_code(val_str)
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('squad_pin_hash', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (hashed,),
            )
            continue
        if k in ENCRYPTED_KEYS:
            enc_val = encrypt_secret(val_str) if val_str else ""
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, enc_val),
            )
        elif k in (
            "require_pin",
            "brand_name",
            "whatsapp_phone",
            "email_recipient",
            "smtp_sender",
            "amadeus_client_id",
            "amadeus_env",
            "travelpayouts_marker",
        ):
            val_clean = val_str.lower() if k in ("require_pin", "amadeus_env") else val_str[:100]
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, val_clean),
            )

    conn.commit()
    conn.close()
    return get_public_settings()


def check_squad_pin(pin: str) -> bool:
    raw = get_settings()
    stored_hash = raw.get("squad_pin_hash", "")
    if not stored_hash:
        return True
    return verify_access_code(pin.strip(), stored_hash)
