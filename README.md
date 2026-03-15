# Apex Algos · PNL Web Platform

Strategy PNL tracker for Tradetron — web dashboard for small groups of traders.

## Stack
- **Backend**: FastAPI + APScheduler (Python)
- **Database**: Supabase (managed Postgres + Auth, free tier)
- **Frontend**: Plain HTML/JS served by FastAPI
- **Deploy**: Railway or Render (free tier)

---

## Setup (15 minutes)

### 1. Supabase
1. Create a free project at https://supabase.com
2. Go to **SQL Editor** → paste and run `supabase_schema.sql`
3. Go to **Project Settings → API**:
   - Copy **Project URL** → `SUPABASE_URL`
   - Copy **service_role** key (not anon!) → `SUPABASE_SERVICE_ROLE_KEY`
4. Go to **Authentication → Settings** → turn on **Email confirmations** (or off for testing)

### 2. Local development
```bash
cd apexalgos

# Install dependencies
pip install -r requirements.txt

# Create .env from template
cp .env.example .env
# Edit .env and fill in SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

# Generate APP_SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Paste the output into .env as APP_SECRET_KEY

# Run
uvicorn backend.main:app --reload --port 8000
# Open http://localhost:8000
```

### 3. Deploy to Railway
```bash
# Install Railway CLI
npm i -g @railway/cli

railway login
railway init
railway up

# Set env vars in Railway dashboard (same as .env)
```

Or deploy to **Render**: connect GitHub repo, set env vars, done.

---

## How it works

1. User signs up → Supabase Auth handles email/password
2. User adds their Tradetron credentials in **Strategies** tab
3. Background poller (APScheduler) runs every minute during market hours (9:15–15:35 IST, Mon–Fri)
4. For each active strategy config, it:
   - Logs into Tradetron using the same ALTCHA solver as your existing bot
   - Fetches all deployed strategies
   - Writes PNL ticks to `pnl_ticks` table
   - Upserts daily high/low/exit to `daily_summary` table
5. Dashboard refreshes automatically every 60 seconds

## File structure
```
apexalgos/
├── backend/
│   ├── main.py          # FastAPI app + scheduler
│   ├── auth.py          # Sign up / sign in routes
│   ├── strategies.py    # Add/remove Tradetron accounts
│   ├── dashboard.py     # Dashboard API routes
│   ├── poller.py        # Background 1-min polling job
│   ├── tradetron.py     # Tradetron API client (from bot)
│   └── db.py            # Supabase client singleton
├── frontend/
│   └── index.html       # Full SPA (auth + dashboard)
├── supabase_schema.sql  # Run this in Supabase SQL editor
├── requirements.txt
└── .env.example
```

## Security notes
- Tradetron passwords are **AES-encrypted** at rest using `cryptography.Fernet`
- Supabase session cookies are cached per strategy and refreshed automatically
- RLS policies ensure users can only see their own data
- JWT tokens from Supabase Auth are verified on every API request
