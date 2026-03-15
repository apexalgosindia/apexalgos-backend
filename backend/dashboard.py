"""
Dashboard API
-------------
GET /api/dashboard/today          — live view for today
GET /api/dashboard/history?days=N — last N days summary
GET /api/dashboard/chart?days=N   — day-by-day PNL for chart
GET /api/dashboard/intraday?date= — 1-min tick data for a specific date
"""

from fastapi import APIRouter, Depends, Query
from datetime import datetime, timedelta
from .db   import get_supabase
from .auth import verify_token

router = APIRouter()


def _ist_today() -> str:
    return (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _user_config_ids(user_id: str) -> list[str]:
    sb = get_supabase()
    rows = sb.table("strategies").select("id").eq("user_id", user_id).eq("is_active", True).execute()
    return [r["id"] for r in (rows.data or [])]


@router.get("/today")
async def today(user: dict = Depends(verify_token)):
    sb   = get_supabase()
    cids = _user_config_ids(user["id"])
    if not cids:
        return {"strategies": [], "portfolio": None}

    today = _ist_today()
    rows  = sb.table("daily_summary") \
        .select("*") \
        .in_("strategy_config_id", cids) \
        .eq("date", today) \
        .order("strategy_name") \
        .execute()

    summaries = rows.data or []

    # Aggregate portfolio-level stats
    if summaries:
        port = {
            "date":       today,
            "total_pnl":  round(sum(r["exit_pnl"] for r in summaries), 2),
            "day_high":   round(sum(r["high"]     for r in summaries), 2),
            "day_low":    round(sum(r["low"]       for r in summaries), 2),
            "strategies": summaries,
        }
    else:
        port = {"date": today, "total_pnl": 0, "day_high": 0, "day_low": 0, "strategies": []}

    return port


@router.get("/history")
async def history(days: int = Query(default=30, le=365), user: dict = Depends(verify_token)):
    sb   = get_supabase()
    cids = _user_config_ids(user["id"])
    if not cids:
        return []

    since = (datetime.utcnow() + timedelta(hours=5, minutes=30) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows  = sb.table("daily_summary") \
        .select("*") \
        .in_("strategy_config_id", cids) \
        .gte("date", since) \
        .order("date") \
        .execute()
    return rows.data or []


@router.get("/chart")
async def chart(days: int = Query(default=14, le=90), user: dict = Depends(verify_token)):
    """Return aggregated daily portfolio PNL for charting."""
    sb   = get_supabase()
    cids = _user_config_ids(user["id"])
    if not cids:
        return []

    since = (datetime.utcnow() + timedelta(hours=5, minutes=30) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows  = sb.table("daily_summary") \
        .select("date,exit_pnl,high,low") \
        .in_("strategy_config_id", cids) \
        .gte("date", since) \
        .order("date") \
        .execute()

    # Group by date, sum across strategies
    by_date: dict = {}
    for r in (rows.data or []):
        d = r["date"]
        if d not in by_date:
            by_date[d] = {"date": d, "pnl": 0, "high": 0, "low": 0}
        by_date[d]["pnl"]  += r["exit_pnl"]
        by_date[d]["high"] += r["high"]
        by_date[d]["low"]  += r["low"]

    return [{"date": k, "pnl": round(v["pnl"], 2),
             "high": round(v["high"], 2), "low": round(v["low"], 2)}
            for k, v in sorted(by_date.items())]


@router.get("/intraday")
async def intraday(date: str = Query(default=""), user: dict = Depends(verify_token)):
    """Return 1-min tick data for a specific date (default = today)."""
    sb   = get_supabase()
    cids = _user_config_ids(user["id"])
    if not cids:
        return []

    target = date or _ist_today()
    prefix = f"{target}T"

    rows = sb.table("pnl_ticks") \
        .select("ts,strategy_name,value") \
        .in_("strategy_config_id", cids) \
        .like("ts", f"{prefix}%") \
        .order("ts") \
        .execute()

    # Group by ts, sum portfolio value
    by_ts: dict = {}
    for r in (rows.data or []):
        ts = r["ts"]
        by_ts.setdefault(ts, {"ts": ts, "total": 0, "strategies": {}})
        by_ts[ts]["strategies"][r["strategy_name"]] = r["value"]
        by_ts[ts]["total"] += r["value"]

    return [{"ts": k, "total": round(v["total"], 2), "strategies": v["strategies"]}
            for k, v in sorted(by_ts.items())]
