"""Google Calendar via OAuth2 PKCE + REST. Stdlib only.

One-time `auth` opens a browser (or prints a URL for remote machines);
token is stored and auto-refreshed after that. No API keys to buy —
a free Google Cloud *desktop* OAuth client JSON is the only credential.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .config import load_token, save_token

SCOPES = "https://www.googleapis.com/auth/calendar.events"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class GCalError(Exception):
    pass


def _client(cfg: dict) -> dict:
    path = cfg.get("client_secret")
    if not path:
        raise GCalError(
            "no OAuth client configured. Run: yapcal auth --client-secret /path/to/client_secret.json\n"
            "(free: Google Cloud Console -> Credentials -> OAuth client ID -> Desktop app)")
    try:
        raw = json.loads(open(path).read())
    except (OSError, json.JSONDecodeError) as e:
        raise GCalError(f"can't read client secret file {path}: {e}")
    key = next((k for k in ("desktop", "installed", "web") if k in raw), next(iter(raw), None))
    if not key:
        raise GCalError(f"unrecognized client secret JSON: {list(raw)}")
    client = raw[key]
    # Desktop/installed clients accept any loopback redirect; pick one we can listen on.
    if key in ("desktop", "installed"):
        client["redirect_uris"] = ["http://localhost:8094/oauth2callback"]
    return client


# ---- auth ------------------------------------------------------------------

def auth(cfg: dict, manual: bool = False) -> None:
    client = _client(cfg)
    redirect = client.get("redirect_uris", ["http://localhost:8094/oauth2callback"])[0]
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect,
        "response_type": "code", "scope": SCOPES, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent",
    })
    print("\nOpen this URL in a browser and approve:\n\n" + url + "\n")

    code = None
    parsed = urllib.parse.urlparse(redirect)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not manual:
        try:
            code = _catch_callback(port, state, timeout=300)
        except Exception as e:
            print(f"(couldn't listen on port {port}: {e})")
    if code is None:
        raw = input("paste the full redirected URL (or just the code): ").strip()
        code = _code_from(raw)
    _exchange(cfg, client, code, verifier, redirect)
    print("✓ authorized. Token saved; it auto-refreshes from now on.")


def _code_from(raw: str) -> str:
    if "code=" in raw:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
        return q["code"][0]
    return raw


def _catch_callback(port: int, state: str, timeout: int) -> str | None:
    holder = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q and q.get("state", [""])[0] == state:
                holder["code"] = q["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"yapcal: you can close this tab.")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("localhost", port), H)
    srv.timeout = timeout
    print(f"(listening on localhost:{port} for the callback — "
          "if your browser is on another machine, paste the URL below instead)")
    srv.handle_request()
    srv.server_close()
    return holder.get("code")


def _exchange(cfg, client, code, verifier, redirect):
    body = urllib.parse.urlencode({
        "code": code, "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "redirect_uri": redirect, "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode()
    try:
        with urllib.request.urlopen(TOKEN_URL, body, timeout=30) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        raise GCalError(f"token exchange failed: {e.read()[:300]!r}")
    tok["_saved_at"] = datetime.now(timezone.utc).isoformat()
    save_token(tok)


# ---- session / REST ---------------------------------------------------------

def access_token(cfg: dict) -> str:
    tok = load_token()
    if not tok:
        raise GCalError("not authorized yet. Run: yapcal auth")
    if "expiry" in tok:
        try:
            exp = datetime.fromisoformat(tok["expiry"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < exp - timedelta(minutes=2):
                return tok["access_token"]
        except ValueError:
            pass
    elif "_saved_at" in tok:
        saved = datetime.fromisoformat(tok["_saved_at"])
        if datetime.now(timezone.utc) < saved + timedelta(seconds=tok.get("expires_in", 3599) - 120):
            return tok["access_token"]
    # refresh
    if "refresh_token" not in tok:
        raise GCalError("token expired and not refreshable. Run: yapcal auth")
    client = _client(cfg)
    body = urllib.parse.urlencode({
        "client_id": client["client_id"], "client_secret": client.get("client_secret", ""),
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    try:
        with urllib.request.urlopen(TOKEN_URL, body, timeout=30) as r:
            new = json.load(r)
    except urllib.error.HTTPError as e:
        raise GCalError(
            "refresh failed — consent expired. Re-run: yapcal auth") from e
    new["refresh_token"] = tok["refresh_token"]  # Google omits it when unchanged
    new["_saved_at"] = datetime.now(timezone.utc).isoformat()
    save_token(new)
    return new["access_token"]


def _api(cfg: dict, method: str, path: str, params: dict | None = None,
         body: dict | None = None) -> dict:
    url = f"https://www.googleapis.com/calendar/v3/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {access_token(cfg)}",
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        raise GCalError(f"calendar API {method} failed: {e.read()[:300]!r}")


def list_events(cfg: dict, time_min: str, time_max: str) -> list[dict]:
    cal = urllib.parse.quote(cfg.get("calendar_id", "primary"))
    out = _api(cfg, "GET", f"calendars/{cal}/events", {
        "timeMin": _rfc3339(time_min), "timeMax": _rfc3339(time_max),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": 100})
    return out.get("items", [])


def _rfc3339(s: str) -> str:
    """Ensure ISO strings carry a timezone (Google requires it)."""
    if s.endswith("Z") or "+" in s[10:] or (len(s) > 13 and s.count("-") > 2):
        return s
    try:
        return datetime.fromisoformat(s).astimezone().isoformat()
    except ValueError:
        return s  # already a plain date (all-day)


def create_event(cfg: dict, summary: str, start: str, end: str,
                 description: str = "", location: str = "",
                 all_day: bool = False) -> dict:
    cal = urllib.parse.quote(cfg.get("calendar_id", "primary"))
    if all_day:
        # start/end are YYYY-MM-DD; Google wants end = day AFTER the last day
        body = {"summary": summary,
                "start": {"date": start}, "end": {"date": end}}
    else:
        body = {"summary": summary,
                "start": {"dateTime": _rfc3339(start)}, "end": {"dateTime": _rfc3339(end)}}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    return _api(cfg, "POST", f"calendars/{cal}/events", body=body)


def delete_event(cfg: dict, event_id: str) -> None:
    cal = urllib.parse.quote(cfg.get("calendar_id", "primary"))
    _api(cfg, "DELETE", f"calendars/{cal}/events/{event_id}")
