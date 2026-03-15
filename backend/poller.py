"""
Poller v2 — master account architecture
- Fetches ALL strategies from master Tradetron account every 1 min
- Maps each strategy SID to users via user_strategies table
- Writes pnl_ticks + daily_summary per user
- Sends EOD Telegram summary at 15:35 IST
"""

import os, asyncio, logging
from datetime import datetime, timedelta
from .db         import get_supabase
from .tradetron  import TradetronClient, calculate_pnl, is_market_hours

log = logging.getLogger(__name__)

_master_client = None
_eod_sent_date = None   # track which date EOD was already sent

def get_master_client():
    global _master_client
    if _master_client is None:
        email    = os.environ.get("MASTER_TT_EMAIL", "")
        password = os.environ.get("MASTER_TT_PASSWORD", "")
        if not email or not password:
            raise RuntimeError("MASTER_TT_EMAIL / MASTER_TT_PASSWORD not set")
        _master_client = TradetronClient(email=email, password=password)
    return _master_client

def ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

async def poll_all_users():
    now      = ist_now()
    today    = now.strftime("%Y-%m-%d")
    now_hhmm = (now.hour, now.minute)

    # EOD summary trigger at 15:35
    await maybe_send_eod(today, now_hhmm)

    if not is_market_hours():
        return

    sb = get_supabase()

    # Fetch all strategies from master Tradetron account
    try:
        client    = get_master_client()
        strategies = await asyncio.to_thread(client.fetch_all_strategies)
        if strategies is None:
            log.warning("Master account session expired — forcing re-login")
            client.logged_in = False
            return
    except Exception as e:
        log.error(f"Poller: master fetch error: {e}")
        return

    pnl_data = calculate_pnl(strategies)
    ts_now   = now.strftime("%Y-%m-%dT%H:%M:%S")

    # Build SID → PNL map from master account
    sid_pnl = {}
    for s in pnl_data["strategies"]:
        if s.get("sid"):
            sid_pnl[str(s["sid"])] = s

    if not sid_pnl:
        log.warning("No strategies with SIDs found on master account")
        return

    # Load all active user_strategies that have a SID mapped
    rows = sb.table("user_strategies").select("id,user_id,name,sid,shared_code") \
        .eq("is_active", True).neq("sid", "").execute()
    user_strats = rows.data or []

    # Also try to map any strategies missing SIDs
    unmapped = sb.table("user_strategies").select("id,user_id,name,shared_code") \
        .eq("is_active", True).eq("sid", "").execute()
    for us in (unmapped.data or []):
        matched_sid = _match_shared_code(us["shared_code"], pnl_data["strategies"])
        if matched_sid:
            sb.table("user_strategies").update({"sid": matched_sid}).eq("id", us["id"]).execute()
            log.info(f"Mapped shared_code {us['shared_code']} → SID {matched_sid}")
            user_strats.append({**us, "sid": matched_sid})

    tick_rows = []
    for us in user_strats:
        sid  = str(us["sid"])
        s    = sid_pnl.get(sid)
        if not s:
            continue
        pnl  = s["today_pnl"]
        uid  = us["user_id"]
        name = us["name"]

        tick_rows.append({"sid": sid, "user_id": uid, "ts": ts_now, "value": pnl})

        # Upsert daily summary
        key = (sid, uid, today)
        existing = sb.table("daily_summary").select("*") \
            .eq("sid", sid).eq("user_id", uid).eq("date", today).execute()

        if existing.data:
            row = existing.data[0]
            update = {"exit_pnl": pnl, "tick_count": (row["tick_count"] or 0) + 1}
            if pnl > (row["high"] or pnl):
                update["high"]       = pnl
                update["peak_value"] = pnl
                update["peak_time"]  = ts_now
            if pnl < (row["low"] or pnl):
                update["low"] = pnl
            sb.table("daily_summary").update(update).eq("id", row["id"]).execute()
        else:
            sb.table("daily_summary").insert({
                "sid": sid, "user_id": uid, "strat_name": name, "date": today,
                "high": pnl, "low": pnl, "exit_pnl": pnl,
                "peak_value": pnl, "peak_time": ts_now, "tick_count": 1
            }).execute()

    if tick_rows:
        sb.table("pnl_ticks").insert(tick_rows).execute()

    log.info(f"✅ Poll done — {len(tick_rows)} ticks across {len(user_strats)} user strategies")


def _match_shared_code(shared_code: str, strategies: list) -> str:
    """Try to find the SID for a shared code by matching strategy name or code field."""
    code = shared_code.strip().lower()
    for s in strategies:
        # Tradetron may return shared_code or share_code field
        for field in ("shared_code", "share_code", "sharedCode", "shareCode"):
            if str(s.get(field, "")).strip().lower() == code:
                return str(s.get("id") or s.get("sid") or "")
    return ""


async def maybe_send_eod(today: str, now_hhmm: tuple):
    global _eod_sent_date
    if now_hhmm < (15, 35) or now_hhmm > (15, 45):
        return
    if _eod_sent_date == today:
        return
    _eod_sent_date = today
    log.info("Sending EOD summaries...")
    try:
        from .telegram_alerts import send_eod_summary
        sb = get_supabase()

        # Get all users with Telegram configured + alert_eod enabled
        settings = sb.table("user_settings").select("*") \
            .eq("alert_eod", True).neq("telegram_bot_token", "").neq("telegram_chat_id", "").execute()

        for cfg in (settings.data or []):
            uid = cfg["user_id"]
            summaries = sb.table("daily_summary").select("*") \
                .eq("user_id", uid).eq("date", today).execute()
            if summaries.data:
                try:
                    await send_eod_summary(
                        cfg["telegram_bot_token"],
                        cfg["telegram_chat_id"],
                        summaries.data,
                        today
                    )
                except Exception as e:
                    log.error(f"EOD alert failed for user {uid}: {e}")
    except Exception as e:
        log.error(f"EOD send error: {e}")
