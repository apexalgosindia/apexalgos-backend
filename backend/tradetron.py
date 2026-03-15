"""
Tradetron client (extracted from pnl bot)
-----------------------------------------
Reuses the bot's ALTCHA solver + login flow verbatim.
calculate_pnl() is the same logic as the bot.
is_market_hours() uses IST.
"""

import re, json, time, base64, hashlib, logging, urllib.request, urllib.parse, http.cookiejar
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

BASE_URL = "https://tradetron.tech"
API_URL  = f"{BASE_URL}/api/deployed-strategies"

MARKET_START = (9, 15)
MARKET_END   = (15, 35)


def ist_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def is_market_hours() -> bool:
    now = ist_now()
    if now.weekday() >= 5:
        return False
    return MARKET_START <= (now.hour, now.minute) <= MARKET_END


def solve_altcha(challenge_data: dict):
    salt      = challenge_data.get("salt", "")
    challenge = challenge_data.get("challenge", "")
    maxnumber = int(challenge_data.get("maxnumber", 1_000_000))
    algorithm = challenge_data.get("algorithm", "SHA-256")
    signature = challenge_data.get("signature", "")
    log.info(f"ALTCHA: solving (maxnumber={maxnumber})...")
    t0 = time.time()
    for n in range(maxnumber + 1):
        if hashlib.sha256(f"{salt}{n}".encode()).hexdigest() == challenge:
            took = int((time.time() - t0) * 1000)
            log.info(f"ALTCHA: solved n={n} in {took}ms")
            sol = {"algorithm": algorithm, "challenge": challenge,
                   "number": n, "salt": salt, "signature": signature}
            b64 = base64.b64encode(json.dumps(sol, separators=(",", ":")).encode()).decode()
            if took < 100:
                time.sleep(0.15)
            return b64, json.dumps(sol, separators=(",", ":"))
    log.error("ALTCHA: no solution found")
    return None, None


class TradetronClient:
    def __init__(self, email: str, password: str,
                 session_cookie: str = "", xsrf_token: str = ""):
        self.email    = email
        self.password = password
        self.jar      = http.cookiejar.CookieJar()
        self.opener   = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.logged_in = False

        if session_cookie and xsrf_token:
            self._set_cookies(session_cookie, urllib.parse.unquote(xsrf_token))
            self.logged_in = True
            log.info("🍪 Manual session cookies loaded")

    def _set_cookies(self, session_val: str, xsrf_val: str):
        for name, val in [("tradetron_session", session_val), ("XSRF-TOKEN", xsrf_val)]:
            self.jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=name, value=val, port=None, port_specified=False,
                domain="tradetron.tech", domain_specified=True, domain_initial_dot=False,
                path="/", path_specified=True, secure=True, expires=None,
                discard=False, comment=None, comment_url=None, rest={}))

    def _xsrf(self) -> str:
        for c in self.jar:
            if c.name == "XSRF-TOKEN":
                return urllib.parse.unquote(c.value)
        return ""

    def get_cookies(self) -> tuple[str, str]:
        """Return (session, xsrf) for caching."""
        session = xsrf = ""
        for c in self.jar:
            if c.name == "tradetron_session": session = c.value
            if c.name == "XSRF-TOKEN":        xsrf    = c.value
        return session, xsrf

    def login(self) -> bool:
        if not self.email or not self.password:
            return False
        log.info("Auto-login: GET /login")
        try:
            html = self.opener.open(urllib.request.Request(
                f"{BASE_URL}/login",
                headers={"User-Agent": "Mozilla/5.0",
                         "Accept": "text/html,application/xhtml+xml"}),
                timeout=20).read().decode("utf-8", errors="ignore")

            xsrf = self._xsrf()
            m = re.search(r'<input[^>]+name=["\']_token["\'][^>]+value=["\']([^"\']+)["\']'
                          r'|<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']_token["\']',
                          html, re.IGNORECASE)
            csrf_token = (m.group(1) or m.group(2)) if m else ""

            challenge_url_m = re.search(r'challengeurl=["\']([^"\']+)["\']', html, re.IGNORECASE)
            b64_sol = json_sol = None
            if challenge_url_m:
                ch_url = challenge_url_m.group(1)
                if not ch_url.startswith("http"):
                    ch_url = f"{BASE_URL}{ch_url}"
                ch_data = json.loads(self.opener.open(urllib.request.Request(
                    ch_url,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                             "Referer": f"{BASE_URL}/login", "X-XSRF-TOKEN": xsrf}),
                    timeout=15).read().decode("utf-8", errors="ignore"))
                b64_sol, json_sol = solve_altcha(ch_data)
                if not b64_sol:
                    log.error("Failed to solve ALTCHA")
                    return False

            for label, altcha_val in [("b64+sig", b64_sol), ("json", json_sol), ("none", None)]:
                if label != "none" and not altcha_val:
                    continue
                payload = {"email": self.email, "password": self.password}
                if csrf_token:  payload["_token"] = csrf_token
                if altcha_val:  payload["altcha"] = altcha_val
                try:
                    data = urllib.parse.urlencode(payload).encode()
                    req  = urllib.request.Request(
                        f"{BASE_URL}/login", data=data,
                        headers={"User-Agent": "Mozilla/5.0",
                                 "Content-Type": "application/x-www-form-urlencoded",
                                 "Referer": f"{BASE_URL}/login",
                                 "X-XSRF-TOKEN": xsrf, "Origin": BASE_URL},
                        method="POST")
                    resp     = self.opener.open(req, timeout=20)
                    resp_url = resp.geturl()
                    if "login" not in resp_url:
                        self.logged_in = True
                        log.info(f"✅ Login successful (variant={label})")
                        return True
                except Exception as e:
                    log.warning(f"Login attempt ({label}) failed: {e}")
            log.error("All login attempts failed")
            return False
        except Exception as e:
            log.error(f"Login error: {e}", exc_info=True)
            return False

    def fetch_all_strategies(self) -> list | None:
        if not self.logged_in:
            if not self.login():
                return None
        xsrf = self._xsrf()
        try:
            req  = urllib.request.Request(
                API_URL,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                         "Referer": BASE_URL, "X-XSRF-TOKEN": xsrf,
                         "X-Requested-With": "XMLHttpRequest"})
            body = self.opener.open(req, timeout=20).read().decode("utf-8", errors="ignore")
            data = json.loads(body)
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            if isinstance(data, list):
                return data
            log.warning(f"Unexpected API response structure")
            self.logged_in = False
            return None
        except Exception as e:
            log.error(f"fetch_all_strategies error: {e}")
            self.logged_in = False
            return None


def calculate_pnl(strategies: list) -> dict:
    """Same logic as the original bot's calculate_pnl()."""
    result, total_today = [], 0.0
    for s in strategies:
        name   = s.get("name") or s.get("strategyName") or "Unknown"
        sid    = s.get("id") or s.get("strategyId") or ""
        status = s.get("deployedStatus") or s.get("status") or "—"
        today_pnl = 0.0
        for key in ("todayPnl", "today_pnl", "pnl", "currentPnl"):
            v = s.get(key)
            if v is not None:
                try:
                    today_pnl = float(v); break
                except (ValueError, TypeError):
                    pass
        total_capital = 0.0
        for key in ("totalCapital", "total_capital", "capital", "deployedCapital"):
            v = s.get(key)
            if v is not None:
                try:
                    total_capital = float(v); break
                except (ValueError, TypeError):
                    pass
        pnl_pct = (today_pnl / total_capital * 100) if total_capital else 0.0
        total_today += today_pnl
        result.append({
            "name": name, "sid": sid, "status": status,
            "today_pnl": round(today_pnl, 2),
            "pnl_pct":   round(pnl_pct, 2),
            "capital":   total_capital,
        })
    return {
        "strategies":      result,
        "total_today_pnl": round(total_today, 2),
        "total_pnl_pct":   0.0,
        "timestamp":       ist_now().isoformat(),
    }
