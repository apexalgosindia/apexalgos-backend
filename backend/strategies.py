"""
Strategies module
-----------------
Users add their Tradetron credentials here.
We store:
  - display name
  - tradetron email + encrypted password  OR  session cookies
  - which specific strategy SIDs to track (optional — blank = all)

Table: strategies
  id          uuid PK
  user_id     uuid FK → auth.users
  name        text        (display label, e.g. "My NAP")
  tt_email    text
  tt_password text        (AES-encrypted with APP_SECRET)
  tt_session  text        (cached session cookie after first login)
  tt_xsrf     text        (cached xsrf)
  strategy_sids text      (comma-sep Tradetron SIDs, blank = all)
  is_active   bool
  created_at  timestamptz
"""

import os, json
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from cryptography.fernet import Fernet
from .db   import get_supabase
from .auth import verify_token

router = APIRouter()


# ── Encryption (password at rest) ─────────────────────────────────────────────

def _fernet() -> Fernet:
    key = os.environ.get("APP_SECRET_KEY", "")
    if not key:
        raise RuntimeError("APP_SECRET_KEY env var not set")
    return Fernet(key.encode() if len(key) == 44 else Fernet.generate_key())

def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()

def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


# ── Schemas ────────────────────────────────────────────────────────────────────

class AddStrategyIn(BaseModel):
    name:          str
    tt_email:      str
    tt_password:   str
    strategy_sids: str = ""   # optional: "123,456" or blank for all

class UpdateCookiesIn(BaseModel):
    strategy_id: str
    tt_session:  str
    tt_xsrf:     str


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_strategies(user: dict = Depends(verify_token)):
    sb = get_supabase()
    rows = sb.table("strategies").select(
        "id,name,tt_email,strategy_sids,is_active,created_at"
    ).eq("user_id", user["id"]).order("created_at").execute()
    return rows.data or []


@router.post("/")
async def add_strategy(body: AddStrategyIn, user: dict = Depends(verify_token)):
    sb = get_supabase()
    # Check limit (20 per user — generous for small group)
    existing = sb.table("strategies").select("id").eq("user_id", user["id"]).execute()
    if len(existing.data or []) >= 20:
        raise HTTPException(400, "Maximum 20 strategies per account")
    row = {
        "user_id":       user["id"],
        "name":          body.name,
        "tt_email":      body.tt_email,
        "tt_password":   encrypt(body.tt_password),
        "strategy_sids": body.strategy_sids,
        "is_active":     True,
    }
    res = sb.table("strategies").insert(row).execute()
    inserted = res.data[0]
    return {"id": inserted["id"], "name": inserted["name"], "message": "Strategy added. First poll in < 1 min."}


@router.delete("/{strategy_id}")
async def delete_strategy(strategy_id: str, user: dict = Depends(verify_token)):
    sb = get_supabase()
    sb.table("strategies").delete().eq("id", strategy_id).eq("user_id", user["id"]).execute()
    return {"message": "Deleted"}


@router.patch("/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: str, user: dict = Depends(verify_token)):
    sb = get_supabase()
    row = sb.table("strategies").select("is_active").eq("id", strategy_id).eq("user_id", user["id"]).single().execute()
    if not row.data:
        raise HTTPException(404, "Not found")
    new_val = not row.data["is_active"]
    sb.table("strategies").update({"is_active": new_val}).eq("id", strategy_id).execute()
    return {"is_active": new_val}
