"""
Strategies v2 — shared code architecture
User submits shared code → backend adds to master Tradetron account → maps SID to user
"""

import os, asyncio, logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from .db   import get_supabase
from .auth import verify_token
from .tradetron import TradetronClient

router = APIRouter()
log    = logging.getLogger(__name__)

_master_client = None

def get_master_client():
    global _master_client
    if _master_client is None:
        email    = os.environ.get("MASTER_TT_EMAIL", "")
        password = os.environ.get("MASTER_TT_PASSWORD", "")
        if not email or not password:
            raise RuntimeError("MASTER_TT_EMAIL and MASTER_TT_PASSWORD not set")
        _master_client = TradetronClient(email=email, password=password)
    return _master_client

class AddStrategyIn(BaseModel):
    name:        str
    shared_code: str

class UpdateSettingsIn(BaseModel):
    telegram_bot_token: str = ""
    telegram_chat_id:   str = ""
    alert_eod:          bool = True

@router.get("/")
async def list_strategies(user: dict = Depends(verify_token)):
    sb = get_supabase()
    rows = sb.table("user_strategies").select(
        "id,name,shared_code,sid,is_active,created_at"
    ).eq("user_id", user["id"]).order("created_at").execute()
    return rows.data or []

@router.post("/")
async def add_strategy(body: AddStrategyIn, user: dict = Depends(verify_token)):
    sb = get_supabase()
    existing = sb.table("user_strategies").select("id").eq("user_id", user["id"]).execute()
    if len(existing.data or []) >= 20:
        raise HTTPException(400, "Maximum 20 strategies per account")
    dup = sb.table("user_strategies").select("id").eq("user_id", user["id"]).eq("shared_code", body.shared_code.strip()).execute()
    if dup.data:
        raise HTTPException(400, "You already added this shared code")
    sid = ""
    try:
        client = get_master_client()
        sid    = await asyncio.to_thread(client.add_shared_strategy, body.shared_code.strip())
        log.info(f"Added shared code {body.shared_code} → SID={sid}")
    except Exception as e:
        log.warning(f"Could not add shared code to master: {e}")
    row = {"user_id": user["id"], "name": body.name,
           "shared_code": body.shared_code.strip(), "sid": sid or "", "is_active": True}
    res = sb.table("user_strategies").insert(row).execute()
    return {"id": res.data[0]["id"], "name": res.data[0]["name"],
            "sid": sid, "message": "Strategy added! Data will appear within 1 minute."}

@router.delete("/{strategy_id}")
async def delete_strategy(strategy_id: str, user: dict = Depends(verify_token)):
    sb = get_supabase()
    sb.table("user_strategies").delete().eq("id", strategy_id).eq("user_id", user["id"]).execute()
    return {"message": "Deleted"}

@router.patch("/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: str, user: dict = Depends(verify_token)):
    sb  = get_supabase()
    row = sb.table("user_strategies").select("is_active").eq("id", strategy_id).eq("user_id", user["id"]).single().execute()
    if not row.data:
        raise HTTPException(404, "Not found")
    new_val = not row.data["is_active"]
    sb.table("user_strategies").update({"is_active": new_val}).eq("id", strategy_id).execute()
    return {"is_active": new_val}

@router.get("/settings")
async def get_settings(user: dict = Depends(verify_token)):
    sb  = get_supabase()
    row = sb.table("user_settings").select("*").eq("user_id", user["id"]).execute()
    if row.data:
        r = row.data[0]
        tok = r.get("telegram_bot_token") or ""
        r["telegram_bot_token_masked"] = tok[:6]+"…"+tok[-4:] if len(tok)>10 else tok
        return r
    return {"user_id": user["id"], "telegram_bot_token": "", "telegram_chat_id": "", "alert_eod": True}

@router.post("/settings")
async def save_settings(body: UpdateSettingsIn, user: dict = Depends(verify_token)):
    sb  = get_supabase()
    row = {"user_id": user["id"], "telegram_bot_token": body.telegram_bot_token,
           "telegram_chat_id": body.telegram_chat_id, "alert_eod": body.alert_eod}
    sb.table("user_settings").upsert(row, on_conflict="user_id").execute()
    if body.telegram_bot_token and body.telegram_chat_id:
        try:
            from .telegram_alerts import send_message
            await send_message(body.telegram_bot_token, body.telegram_chat_id,
                               "✅ <b>Apex Algos</b> — Telegram connected successfully!")
            return {"message": "Settings saved. Test message sent!"}
        except Exception as e:
            return {"message": f"Settings saved but Telegram test failed: {e}"}
    return {"message": "Settings saved."}
