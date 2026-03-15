"""
Dashboard API v2
GET /api/dashboard/today      — today live: combined + per strategy PNL, high, low
GET /api/dashboard/history    — last N days per strategy
GET /api/dashboard/chart      — daily aggregated portfolio PNL for chart
GET /api/dashboard/intraday   — 1-min ticks for today
"""

from fastapi import APIRouter, Depends, Query
from datetime import datetime, timedelta
from .db   import get_supabase
from .auth import verify_token

router = APIRouter()

def _ist_today() -> str:
    return (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")

def _user_sids(user_id: str) -> list[str]:
    sb   = get_supabase()
    rows = sb.table("user_strategies").select("sid,name") \
        .eq("user_id", user_id).eq("is_active", True).neq("sid", "").execute()
    return rows.data or []

@router.get("/today")
async def today(user: dict = Depends(verify_token)):
    sb    = get_supabase()
    today = _ist_today()
    sids  = _user_sids(user["id"])
    if not sids:
        return {"date": today, "total_pnl": 0, "total_high": 0, "total_low": 0,
                "strategies": [], "no_strategies": True}

    sid_list = [s["sid"] for s in sids]
    rows     = sb.table("daily_summary").select("*") \
        .eq("user_id", user["id"]).eq("date", today) \
        .in_("sid", sid_list).order("strat_name").execute()

    summaries   = rows.data or []
    total_pnl   = round(sum(s["exit_pnl"] for s in summaries), 2)
    total_high  = round(sum(s["high"]     for s in summaries), 2)
    total_low   = round(sum(s["low"]      for s in summaries), 2)

    return {
        "date":       today,
        "total_pnl":  total_pnl,
        "total_high": total_high,
        "total_low":  total_low,
        "strategies": summaries,
    }

@router.get("/history")
async def history(days: int = Query(default=30, le=365), user: dict = Depends(verify_token)):
    sb    = get_supabase()
    since = (datetime.utcnow() + timedelta(hours=5, minutes=30) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows  = sb.table("daily_summary").select("*") \
        .eq("user_id", user["id"]).gte("date", since).order("date").execute()
    return rows.data or []

@router.get("/chart")
async def chart(days: int = Query(default=14, le=90), user: dict = Depends(verify_token)):
    sb    = get_supabase()
    since = (datetime.utcnow() + timedelta(hours=5, minutes=30) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows  = sb.table("daily_summary").select("date,exit_pnl,high,low") \
        .eq("user_id", user["id"]).gte("date", since).order("date").execute()
    by_date: dict = {}
    for r in (rows.data or []):
        d = r["date"]
        if d not in by_date:
            by_date[d] = {"date": d, "pnl": 0, "high": 0, "low": 0}
        by_date[d]["pnl"]  += r["exit_pnl"]
        by_date[d]["high"] += r["high"]
        by_date[d]["low"]  += r["low"]
    return [{"date": k, "pnl": round(v["pnl"],2),
             "high": round(v["high"],2), "low": round(v["low"],2)}
            for k, v in sorted(by_date.items())]

@router.get("/intraday")
async def intraday(date: str = Query(default=""), user: dict = Depends(verify_token)):
    sb      = get_supabase()
    target  = date or _ist_today()
    prefix  = f"{target}T"
    sids    = _user_sids(user["id"])
    if not sids:
        return []
    sid_map  = {s["sid"]: s["name"] for s in sids}
    sid_list = list(sid_map.keys())
    rows = sb.table("pnl_ticks").select("ts,sid,value") \
        .eq("user_id", user["id"]).in_("sid", sid_list) \
        .like("ts", f"{prefix}%").order("ts").execute()
    by_ts: dict = {}
    for r in (rows.data or []):
        ts = r["ts"]
        by_ts.setdefault(ts, {"ts": ts, "total": 0, "strategies": {}})
        sname = sid_map.get(r["sid"], r["sid"])
        by_ts[ts]["strategies"][sname] = r["value"]
        by_ts[ts]["total"] += r["value"]
    return [{"ts": k, "total": round(v["total"],2), "strategies": v["strategies"]}
            for k, v in sorted(by_ts.items())]
