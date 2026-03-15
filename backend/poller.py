"""
Background poller
-----------------
Runs every 1 minute via APScheduler.
For each active strategy config:
  1. Login to Tradetron (reuses bot logic exactly)
  2. Fetch all deployed strategies
  3. Write pnl_ticks rows
  4. Upsert daily_summary (high/low/peak/exit per strategy per day)

Tables:
  pnl_ticks (id, strategy_config_id, strategy_name, ts, value)
  daily_summary (strategy_config_id, strategy_name, date,
                 high, low, exit_pnl, peak_time, peak_value, tick_count)
"""

import asyncio, logging
from datetime import datetime, timedelta, date
from .db import get_supabase
from .strategies import decrypt
from .tradetron import TradetronClient, calculate_pnl, is_market_hours

log = logging.getLogger(__name__)

# In-memory state: {strategy_config_id: {day_high, day_low, api_client, last_date}}
_state: dict = {}


async def poll_all_users():
    """Called every minute by scheduler."""
    if not is_market_hours():
        return

    sb = get_supabase()
    try:
        rows = sb.table("strategies").select("*").eq("is_active", True).execute()
        configs = rows.data or []
    except Exception as e:
        log.error(f"Poller: failed to fetch strategies: {e}")
        return

    for cfg in configs:
        try:
            await poll_one(cfg)
        except Exception as e:
            log.error(f"Poller: error for strategy_config {cfg['id']}: {e}", exc_info=True)


async def poll_one(cfg: dict):
    sb   = get_supabase()
    cid  = cfg["id"]
    today = datetime.utcnow() + timedelta(hours=5, minutes=30)
    today_str = today.strftime("%Y-%m-%d")

    # Init or reset daily state
    state = _state.setdefault(cid, {"day_high": None, "day_low": None,
                                     "client": None, "last_date": None})
    if state["last_date"] != today_str:
        state["day_high"] = state["day_low"] = None
        state["last_date"] = today_str
        state["client"] = None   # force fresh login each day

    # Build / reuse API client
    if state["client"] is None:
        password = decrypt(cfg["tt_password"])
        state["client"] = TradetronClient(
            email=cfg["tt_email"],
            password=password,
            session_cookie=cfg.get("tt_session") or "",
            xsrf_token=cfg.get("tt_xsrf") or "",
        )

    client: TradetronClient = state["client"]

    # Fetch strategies from Tradetron
    strategies = await asyncio.to_thread(client.fetch_all_strategies)
    if strategies is None:
        log.warning(f"[{cid}] Session expired — will retry next minute")
        client.logged_in = False
        return

    # Cache refreshed cookies back to DB
    new_session, new_xsrf = client.get_cookies()
    if new_session and new_session != cfg.get("tt_session"):
        sb.table("strategies").update(
            {"tt_session": new_session, "tt_xsrf": new_xsrf}
        ).eq("id", cid).execute()

    # Filter to requested SIDs if specified
    sid_filter = [s.strip() for s in (cfg.get("strategy_sids") or "").split(",") if s.strip()]

    pnl_data = calculate_pnl(strategies)
    ts_now   = today.strftime("%Y-%m-%dT%H:%M:%S")

    tick_rows    = []
    summary_map  = {}

    for s in pnl_data["strategies"]:
        sname = s["name"]
        if sid_filter and str(s.get("sid", "")) not in sid_filter:
            continue

        pnl = s["today_pnl"]

        # Track daily high/low at portfolio level
        if state["day_high"] is None or pnl > state["day_high"]:
            state["day_high"] = pnl
        if state["day_low"] is None or pnl < state["day_low"]:
            state["day_low"] = pnl

        tick_rows.append({
            "strategy_config_id": cid,
            "strategy_name":      sname,
            "ts":                 ts_now,
            "value":              pnl,
        })

        # Build/update daily summary entry
        key = (cid, sname, today_str)
        if key not in summary_map:
            # Fetch existing summary from DB for today
            existing = sb.table("daily_summary") \
                .select("*") \
                .eq("strategy_config_id", cid) \
                .eq("strategy_name", sname) \
                .eq("date", today_str) \
                .execute()
            summary_map[key] = existing.data[0] if existing.data else {
                "strategy_config_id": cid,
                "strategy_name":      sname,
                "date":               today_str,
                "high":               pnl,
                "low":                pnl,
                "exit_pnl":           pnl,
                "peak_value":         pnl,
                "peak_time":          ts_now,
                "tick_count":         0,
            }

        row = summary_map[key]
        row["exit_pnl"]   = pnl                           # always latest
        row["tick_count"] = (row.get("tick_count") or 0) + 1
        if pnl > (row.get("high") or pnl):
            row["high"]       = pnl
            row["peak_value"] = pnl
            row["peak_time"]  = ts_now
        if pnl < (row.get("low") or pnl):
            row["low"] = pnl

    # Batch write ticks
    if tick_rows:
        try:
            sb.table("pnl_ticks").insert(tick_rows).execute()
        except Exception as e:
            log.error(f"[{cid}] tick insert error: {e}")

    # Upsert daily summaries
    for row in summary_map.values():
        try:
            sb.table("daily_summary").upsert(row, on_conflict="strategy_config_id,strategy_name,date").execute()
        except Exception as e:
            log.error(f"[{cid}] summary upsert error: {e}")

    log.info(f"[{cid}] ✅ Poll done — {len(tick_rows)} ticks, portfolio ₹{pnl_data['total_today_pnl']:,.0f}")
