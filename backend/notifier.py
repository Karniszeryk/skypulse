import asyncio
import smtplib
import urllib.parse
from email.message import EmailMessage
from typing import List, Dict, Any
import httpx
from backend.database import get_settings


def _send_smtp_email_sync(
    sender_email: str,
    app_password: str,
    recipient_email: str,
    subject: str,
    plain_body: str,
    html_body: str,
) -> bool:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"SkyPulse.pl Radar Lotów <{sender_email}>"
    msg["To"] = recipient_email
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    host = "smtp.gmail.com"
    if "@wp.pl" in sender_email:
        host = "smtp.wp.pl"
    elif "@onet.pl" in sender_email:
        host = "smtp.poczta.onet.pl"
    elif "@interia.pl" in sender_email:
        host = "poczta.interia.pl"
    elif "@o2.pl" in sender_email:
        host = "poczta.o2.pl"

    with smtplib.SMTP_SSL(host, 465, timeout=10) as smtp:
        smtp.login(sender_email, app_password)
        smtp.send_message(msg)
    return True


async def send_deals_notification(
    deals: List[Dict[str, Any]],
    title: str = "SkyPulse.pl – Znalezione okazje lotnicze",
    group_size: int = 1,
) -> Dict[str, Any]:
    settings = get_settings()
    discord_url = (settings.get("discord_webhook_url") or "").strip()
    tg_token = (settings.get("telegram_bot_token") or "").strip()
    tg_chat_id = (settings.get("telegram_chat_id") or "").strip()
    wa_phone = "".join(ch for ch in (settings.get("whatsapp_phone") or "") if ch.isdigit())
    wa_apikey = (settings.get("whatsapp_apikey") or "").strip()
    email_to = (settings.get("email_recipient") or "").strip()
    smtp_sender = (settings.get("smtp_sender") or "").strip()
    smtp_pass = (settings.get("smtp_password") or "").strip()

    if not deals:
        return {"sent": False, "reason": "Brak lotów do wysłania."}

    top_deals = deals[:5]
    lines = [f"*{title}*\n"]
    plain_lines = [f"{title}\n"]
    html_rows = []

    for idx, d in enumerate(top_deals, 1):
        dates_str = (
            f"{d['outbound_departure'][:10]} – {d['inbound_departure'][:10]} ({d['nights']} nocy)"
            if d.get("inbound_departure")
            else d["outbound_departure"][:10]
        )
        grp_info = (
            f" (razem dla {group_size} os.: {round(d['price_pln'] * group_size, 0):.0f} zł)"
            if group_size > 1
            else ""
        )
        bag_info = d.get("baggage_label") or "Mały plecak w cenie"

        lines.append(
            f"{idx}. **{d['origin_city']} ({d['origin_iata']}) → {d['dest_city']} ({d['dest_iata']})**\n"
            f"   Cena: **{d['price_pln']:.0f} zł/os.**{grp_info} • {d['carrier_name']} • `{dates_str}`\n"
            f"   Bagaż: {bag_info}\n"
            f"   Link do biletu: {d['primary_booking_url']}"
        )
        plain_lines.append(
            f"{idx}. {d['origin_city']} ({d['origin_iata']}) -> {d['dest_city']} ({d['dest_iata']})\n"
            f"   Termin: {dates_str} | Linia: {d['carrier_name']}\n"
            f"   Bagaż: {bag_info}\n"
            f"   Cena: {d['price_pln']:.0f} zł / os.{grp_info}\n"
            f"   Zobacz bilet: {d['primary_booking_url']}\n"
        )
        html_rows.append(
            f"""
            <div style="border:1px solid #d8d2c5;border-radius:12px;padding:16px;margin-bottom:14px;background:#ffffff;font-family:sans-serif;color:#1e293b;">
              <div style="font-size:18px;font-weight:bold;color:#1e293b;">
                {idx}. {d['origin_city']} ({d['origin_iata']}) &rarr; {d['dest_city']} ({d['dest_iata']})
              </div>
              <div style="font-size:14px;color:#475569;margin-top:4px;">
                Linia: <strong>{d['carrier_name']}</strong> &bull; Termin: <strong>{dates_str}</strong>
              </div>
              <div style="font-size:13px;color:#64748b;margin-top:4px;">
                Bagaż: {bag_info} &bull; {d.get('layover_summary', 'Lot bezpośredni')}
              </div>
              <div style="font-size:20px;font-weight:800;color:#059669;margin-top:10px;">
                {d['price_pln']:.0f} zł / os. <span style="font-size:14px;font-weight:normal;color:#475569;">{grp_info}</span>
              </div>
              <div style="margin-top:12px;">
                <a href="{d['primary_booking_url']}" style="display:inline-block;background:#2563eb;color:#ffffff;text-decoration:none;font-weight:bold;font-size:14px;padding:10px 18px;border-radius:8px;">
                  Kliknij tutaj, aby zobaczyć lot i kupić bilet &rarr;
                </a>
              </div>
            </div>
            """
        )

    message_text = "\n".join(lines)
    plain_text = "\n".join(plain_lines)
    html_full = f"""
    <html>
      <body style="background:#f4f1ea;padding:20px;font-family:sans-serif;">
        <div style="max-width:600px;margin:0 auto;">
          <h2 style="color:#1e293b;margin-bottom:16px;">{title}</h2>
          {''.join(html_rows)}
        </div>
      </body>
    </html>
    """

    encoded_wa = urllib.parse.quote(plain_text)
    wa_share_url = f"https://wa.me/{wa_phone}?text={encoded_wa}" if wa_phone else f"https://wa.me/?text={encoded_wa}"
    mailto_url = (
        f"mailto:{email_to}?subject={urllib.parse.quote(title)}&body={urllib.parse.quote(plain_text)}"
    )

    results = {"discord": None, "telegram": None, "whatsapp_auto": None, "email_smtp": None}

    async with httpx.AsyncClient() as client:
        if discord_url:
            try:
                r = await client.post(discord_url, json={"content": message_text}, timeout=8.0)
                results["discord"] = r.status_code in (200, 204)
            except Exception as e:
                results["discord"] = f"Błąd: {e}"

        if tg_token and tg_chat_id:
            try:
                tg_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
                r = await client.post(
                    tg_url,
                    json={
                        "chat_id": tg_chat_id,
                        "text": message_text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                    timeout=8.0,
                )
                results["telegram"] = r.status_code == 200
            except Exception as e:
                results["telegram"] = f"Błąd: {e}"

        if wa_phone and wa_apikey:
            try:
                wa_api_url = (
                    f"https://api.callmebot.com/whatsapp.php"
                    f"?phone={wa_phone}&text={encoded_wa}&apikey={urllib.parse.quote(wa_apikey)}"
                )
                r = await client.get(wa_api_url, timeout=10.0)
                results["whatsapp_auto"] = r.status_code == 200
            except Exception as e:
                results["whatsapp_auto"] = f"Błąd: {e}"

    if email_to and smtp_sender and smtp_pass:
        try:
            await asyncio.to_thread(
                _send_smtp_email_sync,
                smtp_sender,
                smtp_pass,
                email_to,
                title,
                plain_text,
                html_full,
            )
            results["email_smtp"] = True
        except Exception as e:
            results["email_smtp"] = f"Błąd SMTP: {e}"

    any_sent = any(v is True for v in results.values())
    return {
        "sent": any_sent,
        "results": results,
        "whatsapp_share_url": wa_share_url,
        "mailto_url": mailto_url,
        "preview": plain_text,
        "reason": None if any_sent else "Brak skonfigurowanej automatycznej bramki – użyj bezpośredniego przycisku WhatsApp lub E-mail.",
    }
