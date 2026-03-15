"""
Telegram alerts
---------------
send_message()     — generic send (used for test + alerts)
send_eod_summary() — called at 15:35 IST for each user who has alerts enabled
"""

import logging
import urllib.request, urllib.parse, json
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

BASE = "https://api.telegram.org/bot"


async def send_message(bot_token: str, chat_id: str, text: str):
    """Send a Telegram message. Raises on failure."""
    url     = f"{BASE}{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id":                  chat_id,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req  = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    if not data.get("ok"):
        raise Exception(f"Telegram error: {data}")


def _inr(v) -> str:
    if v is None: return "—"
    a = abs(v)
    s = "+" if v >= 0 else "−"
    if a >= 100000: return f"{s}₹{a/100000:.2f}L"
    if a >= 1000:   return f"{s}₹{a/1000:.1f}K"
    return f"{s}₹{a:.0f}"


async def send_eod_summary(bot_token: str, chat_id: str, summaries: list, date: str):
    """
    summaries = list of daily_summary rows for this user for today
    """
    if not summaries:
        return

    total_pnl  = sum(s["exit_pnl"]  for s in summaries)
    total_high = sum(s["high"]       for s in summaries)
    total_low  = sum(s["low"]        for s in summaries)

    lines = [
        f"📊 <b>Apex Algos · EOD Summary</b>",
        f"🗓️ {date}",
        "",
        f"<b>Portfolio</b>",
        f"  PNL   : {_inr(total_pnl)}",
        f"  High  : {_inr(total_high)}",
        f"  Low   : {_inr(total_low)}",
        "",
        "<b>Strategies</b>",
    ]
    for s in summaries:
        peak_t = (s.get("peak_time") or "")
        peak_t = peak_t[11:16] if len(peak_t) >= 16 else "—"
        lines.append(
            f"  • {s['strat_name']}\n"
            f"    PNL {_inr(s['exit_pnl'])}  H {_inr(s['high'])}  L {_inr(s['low'])}  Peak {peak_t}"
        )

    await send_message(bot_token, chat_id, "\n".join(lines))
    log.info(f"EOD summary sent to chat {chat_id}")
