"""
Tradetron client — extracted from pnl bot, adapted for master account + shared strategies
"""

import re, json, time, base64, hashlib, logging, urllib.request, urllib.parse, http.cookiejar
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

BASE_URL = "https://tradetron.tech"
API_URL  = f"{BASE_URL}/api/deployed-strategies"

MARKET_START = (9, 15)
MARKET_END   = (15, 35)


def ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def is_market_hours():
    now = ist_now()
    if now.weekday() >= 5:
        return False
    return MARKET_START <= (now.hour, now.minute) <= MARKET_END


def solve_altcha(challenge_data):
    salt = challenge_data.get("salt",""); challenge = challenge_data.get("challenge","")
    maxnumber = int(challenge_data.get("maxnumber", 1_000_000))
    algorithm = challenge_data.get("algorithm","SHA-256"); signature = challenge_data.get("signature","")
    log.info(f"ALTCHA: solving (maxnumber={maxnumber})...")
    t0 = time.time()
    for n in range(maxnumber + 1):
        if hashlib.sha256(f"{salt}{n}".encode()).hexdigest() == challenge:
            took = int((time.time()-t0)*1000)
            log.info(f"ALTCHA: solved n={n} in {took}ms")
            sol = {"algorithm":algorithm,"challenge":challenge,"number":n,"salt":salt,"signature":signature}
            b64 = base64.b64encode(json.dumps(sol,separators=(",",":")).encode()).decode()
            if took < 100: time.sleep(0.15)
            return b64, json.dumps(sol,separators=(",",":"))
    log.error("ALTCHA: no solution found"); return None, None


class TradetronClient:
    def __init__(self, email="", password="", session_cookie="", xsrf_token=""):
        self.email    = email
        self.password = password
        self.jar      = http.cookiejar.CookieJar()
        self.opener   = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.logged_in = False
        if session_cookie and xsrf_token:
            self._set_cookies(session_cookie, urllib.parse.unquote(xsrf_token))
            self.logged_in = True

    def _set_cookies(self, session_val, xsrf_val):
        for name, val in [("tradetron_session", session_val), ("XSRF-TOKEN", xsrf_val)]:
            self.jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=name, value=val, port=None, port_specified=False,
                domain="tradetron.tech", domain_specified=True, domain_initial_dot=False,
                path="/", path_specified=True, secure=True, expires=None,
                discard=False, comment=None, comment_url=None, rest={}))

    def _xsrf(self):
        for c in self.jar:
            if c.name == "XSRF-TOKEN": return urllib.parse.unquote(c.value)
        return ""

    def get_cookies(self):
        session = xsrf = ""
        for c in self.jar:
            if c.name == "tradetron_session": session = c.value
            if c.name == "XSRF-TOKEN":        xsrf    = c.value
        return session, xsrf

    def login(self):
        if not self.email or not self.password: return False
        log.info("Auto-login: GET /login")
        try:
            html = self.opener.open(urllib.request.Request(f"{BASE_URL}/login",
                headers={"User-Agent":"Mozilla/5.0","Accept":"text/html,application/xhtml+xml"}),
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
                if not ch_url.startswith("http"): ch_url = f"{BASE_URL}{ch_url}"
                ch_data = json.loads(self.opener.open(urllib.request.Request(ch_url,
                    headers={"User-Agent":"Mozilla/5.0","Accept":"application/json",
                             "Referer":f"{BASE_URL}/login","X-XSRF-TOKEN":xsrf}),
                    timeout=15).read().decode("utf-8", errors="ignore"))
                b64_sol, json_sol = solve_altcha(ch_data)
                if not b64_sol: log.error("Failed to solve ALTCHA"); return False
            for label, altcha_val in [("b64+sig", b64_sol), ("json", json_sol), ("none", None)]:
                if label != "none" and not altcha_val: continue
                payload = {"email": self.email, "password": self.password}
                if csrf_token: payload["_token"] = csrf_token
                if altcha_val: payload["altcha"] = altcha_val
                try:
                    data = urllib.parse.urlencode(payload).encode()
                    req  = urllib.request.Request(f"{BASE_URL}/login", data=data,
                        headers={"User-Agent":"Mozilla/5.0",
                                 "Content-Type":"application/x-www-form-urlencoded",
                                 "Referer":f"{BASE_URL}/login","X-XSRF-TOKEN":xsrf,
                                 "Origin":BASE_URL},method="POST")
                    resp = self.opener.open(req, timeout=20)
                    if "login" not in resp.geturl():
                        self.logged_in = True
                        log.info(f"✅ Login successful (variant={label})")
                        return True
                except Exception as e:
                    log.warning(f"Login attempt ({label}) failed: {e}")
            log.error("All login attempts failed"); return False
        except Exception as e:
            log.error(f"Login error: {e}", exc_info=True); return False

    def _fetch_page(self, execution_filter, page=1):
        """Fetch one page of deployed strategies with given execution filter."""
        xsrf   = self._xsrf()
        params = urllib.parse.urlencode({
            "tags":"","creator_id":"","execution": execution_filter,"page": page
        })
        req = urllib.request.Request(f"{API_URL}?{params}",
            headers={"User-Agent":"Mozilla/5.0","Accept":"application/json",
                     "X-XSRF-TOKEN":xsrf,"X-Requested-With":"XMLHttpRequest",
                     "Referer":f"{BASE_URL}/deployed-strategies"})
        try:
            body = self.opener.open(req, timeout=30).read().decode("utf-8", errors="ignore")
            data = json.loads(body)
            if data.get("success"): return data
            self.logged_in = False; return None
        except Exception as e:
            log.error(f"_fetch_page error: {e}"); self.logged_in = False; return None

    def _fetch_all_pages(self, execution_filter):
        """Fetch all pages for a given execution filter."""
        results = []; page = 1
        while True:
            data = self._fetch_page(execution_filter, page)
            if not data: return None
            items = data.get("data", {})
            if isinstance(items, dict):
                strategies = items.get("data", []); last_page = items.get("last_page", 1)
            else:
                strategies = items; last_page = 1
            results.extend(strategies)
            log.info(f"Fetched {len(results)} strategies (filter={execution_filter}, page={page}/{last_page})")
            if page >= last_page: break
            page += 1
        return results

    def fetch_shared_strategies(self):
        """
        Fetch all 'Shared with me' strategies using the exact Tradetron endpoint:
        GET /api/shared-strategies?type=Shared with me&mode=Pro&per_page=100&page=1
        Returns list of strategies with SIDs, or None on auth failure.
        """
        if not self.logged_in:
            if not self.login(): return None
        xsrf = self._xsrf()
        all_strats = []
        page = 1
        while True:
            try:
                params = urllib.parse.urlencode({
                    "tags": "", "creator_id": "", "execution": "", "pnl": "",
                    "exchange": "", "instrument_type": "", "status": "",
                    "broker_id": "", "show_nil_qty": "yes",
                    "per_page": 100, "type": "Shared with me",
                    "mode": "Pro", "statuses": "", "page": page
                })
                req = urllib.request.Request(
                    f"{BASE_URL}/api/shared-strategies?{params}",
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                             "X-XSRF-TOKEN": xsrf, "X-Requested-With": "XMLHttpRequest",
                             "Referer": f"{BASE_URL}/deployed-strategies"})
                body = self.opener.open(req, timeout=30).read().decode("utf-8", errors="ignore")
                data = json.loads(body)
                # Handle paginated response
                items = data.get("data", data)
                if isinstance(items, dict):
                    strategies = items.get("data", [])
                    last_page  = items.get("last_page", 1)
                elif isinstance(items, list):
                    strategies = items
                    last_page  = 1
                else:
                    break
                all_strats.extend(strategies)
                log.info(f"Shared strategies: fetched {len(all_strats)} (page {page}/{last_page})")
                if page >= last_page: break
                page += 1
            except Exception as e:
                log.error(f"fetch_shared_strategies page {page} error: {e}")
                self.logged_in = False
                return None
        return all_strats

    def fetch_all_strategies(self):
        """
        Fetch all strategies from master account.
        Combines own deployed strategies + shared-with-me strategies.
        Returns combined list with SIDs, or None on auth failure.
        """
        if not self.logged_in:
            if not self.login(): return None

        # Own deployed strategies (same as original bot)
        own = self._fetch_all_pages("LIVE AUTO,SELF")
        if own is None: return None

        # Shared strategies via dedicated endpoint
        shared = self.fetch_shared_strategies()
        if shared is None:
            shared = []

        combined = own + shared
        log.info(f"Total strategies fetched: {len(combined)} ({len(own)} own + {len(shared)} shared)")
        return combined if combined else None

    def accept_shared_strategy(self, shared_code: str) -> str:
        """
        Accept a shared strategy using the exact Tradetron endpoint:
        POST /user/manage/access/add/code
        Returns SID string on success, empty string on failure.
        """
        if not self.logged_in:
            if not self.login(): return ""
        xsrf = self._xsrf()
        url  = f"{BASE_URL}/user/manage/access/add/code"
        try:
            # Try JSON body first, then form-encoded
            for content_type, payload in [
                ("application/json",                json.dumps({"access_code": shared_code}).encode()),
                ("application/x-www-form-urlencoded", urllib.parse.urlencode({"access_code": shared_code}).encode()),
            ]:
                req = urllib.request.Request(url,
                    data=payload,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                             "Content-Type": content_type, "X-XSRF-TOKEN": xsrf,
                             "X-Requested-With": "XMLHttpRequest",
                             "Referer": f"{BASE_URL}/user/manage/access",
                             "Origin": BASE_URL},
                    method="POST")
                try:
                    body   = self.opener.open(req, timeout=20).read().decode("utf-8", errors="ignore")
                    result = json.loads(body)
                    log.info(f"accept_shared_strategy response: {str(result)[:300]}")
                    sid = (str(result.get("data", {}).get("id", "")) or
                           str(result.get("id", "")) or
                           str(result.get("strategy_id", "")) or "")
                    if sid and sid != "0":
                        log.info(f"✅ Shared strategy accepted, SID={sid}")
                        return sid
                except Exception as e:
                    log.warning(f"accept attempt ({content_type}) failed: {e}")
        except Exception as e:
            log.error(f"accept_shared_strategy error: {e}")
        log.warning(f"Could not auto-accept {shared_code} — please accept manually on Tradetron")
        return ""


def calculate_pnl(strategies):
    """
    Same logic as original bot.
    Uses template.name for display, s['id'] as SID.
    """
    result = []; total = 0.0
    for s in strategies:
        # Name: prefer template.name, fallback to s.name
        raw_name = s.get("template",{}).get("name") or s.get("name","Unknown")
        name     = raw_name.split("|")[0].strip()
        sid      = str(s.get("id") or "")
        status   = s.get("status","—")
        # PNL: bot uses sum_of_pnl / multiplier = 1x PNL
        multiplier = float(s.get("minimum_multiple") or 1) or 1
        pnl_full   = float(s.get("sum_of_pnl") or 0)
        today_pnl  = round(pnl_full / multiplier, 2)
        capital    = float(s.get("template",{}).get("capital_required") or 0)
        pnl_pct    = round((today_pnl / capital) * 100, 2) if capital else 0.0
        total += today_pnl
        result.append({
            "name": name, "sid": sid, "status": status,
            "today_pnl": today_pnl, "pnl_pct": pnl_pct, "capital": capital,
        })
    return {"strategies": result, "total_today_pnl": round(total,2),
            "timestamp": ist_now().isoformat()}
