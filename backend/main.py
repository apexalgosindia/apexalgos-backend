"""
Apex Algos  ·  PNL Web Platform
================================
FastAPI + Supabase backend.
- User auth (JWT via Supabase)
- Add Tradetron strategies (email+password login)
- Background 1-min poller during market hours
- REST API for dashboard
"""

import os, asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from .auth   import router as auth_router,   verify_token
from .strategies import router as strat_router
from .dashboard  import router as dash_router
from .poller  import poll_all_users

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background poller — every 1 minute
    scheduler.add_job(poll_all_users, IntervalTrigger(minutes=1), id="poller", replace_existing=True)
    scheduler.start()
    log.info("✅ Scheduler started (1-min poll)")
    yield
    scheduler.shutdown(wait=False)

app = FastAPI(title="Apex Algos", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(auth_router,  prefix="/api/auth",       tags=["auth"])
app.include_router(strat_router, prefix="/api/strategies", tags=["strategies"])
app.include_router(dash_router,  prefix="/api/dashboard",  tags=["dashboard"])

# Serve frontend
FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND, "static")), name="static")

@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str):
    """Catch-all — serve the SPA for any non-API route."""
    return FileResponse(os.path.join(FRONTEND, "index.html"))
