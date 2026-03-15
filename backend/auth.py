"""
Auth module
-----------
Uses Supabase built-in auth (email+password).
We just proxy sign-up / sign-in and return the JWT.
The JWT is then sent as Bearer token on all protected routes.
"""

import os
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from .db import get_supabase

router  = APIRouter()
_bearer = HTTPBearer()


# ── Schemas ────────────────────────────────────────────────────────────────────

class SignUpIn(BaseModel):
    email:    EmailStr
    password: str
    name:     str = ""

class SignInIn(BaseModel):
    email:    EmailStr
    password: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def verify_token(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    """Validate Supabase JWT and return the user dict."""
    sb = get_supabase()
    try:
        user = sb.auth.get_user(creds.credentials)
        if not user or not user.user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return {"id": user.user.id, "email": user.user.email}
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/signup")
async def signup(body: SignUpIn):
    sb = get_supabase()
    try:
        res = sb.auth.sign_up({"email": body.email, "password": body.password,
                               "options": {"data": {"name": body.name}}})
        if res.user is None:
            raise HTTPException(400, "Sign-up failed — check your email format")
        return {"message": "Account created. Check your email to confirm.", "user_id": res.user.id}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/signin")
async def signin(body: SignInIn):
    sb = get_supabase()
    try:
        res = sb.auth.sign_in_with_password({"email": body.email, "password": body.password})
        return {
            "access_token":  res.session.access_token,
            "refresh_token": res.session.refresh_token,
            "user": {"id": res.user.id, "email": res.user.email,
                     "name": (res.user.user_metadata or {}).get("name", "")},
        }
    except Exception as e:
        raise HTTPException(401, "Invalid email or password")


@router.post("/refresh")
async def refresh(refresh_token: str):
    sb = get_supabase()
    try:
        res = sb.auth.refresh_session(refresh_token)
        return {"access_token": res.session.access_token, "refresh_token": res.session.refresh_token}
    except Exception as e:
        raise HTTPException(401, str(e))


@router.get("/me")
async def me(user: dict = Depends(verify_token)):
    return user
