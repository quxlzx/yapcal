"""Discord bot mode: DM the bot, it manages your calendar.

Stdlib-only: uses Discord's REST API with a long-poll loop (no websockets,
no pip installs). The bot reads DMs from allowed user IDs only.

  python -m yapcal bot --token <BOT_TOKEN> --allow <YOUR_DISCORD_USER_ID>
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import dates, gcal
from .config import load_config, save_config
from .llm import LLM, LLMError
from .parse import parse_command

API = "https://discord.com/api/v10"


class DiscordError(Exception):
    pass


class Discord:
    def __init__(self, token: str):
        self.token = token

    def _req(self, method: str, path: str, body: dict | None = None,
             params: dict | None = None):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            raise DiscordError(f"discord {method} {path}: {e.code} {e.read()[:200]!r}")

    def whoami(self) -> dict:
        return self._req("GET", "/users/@me")

    def dm_channels(self) -> list[dict]:
        return [c for c in self._req("GET", "/users/@me/channels")
                if c.get("type") == 1]

    def messages(self, channel: str, after: str) -> list[dict]:
        params = {"limit": 25}
        if after:
            params["after"] = after
        return self._req("GET", f"/channels/{channel}/messages", params=params)

    def send(self, channel: str, text: str) -> None:
        # chunk to Discord's 2000-char limit
        for i in range(0, len(text), 1990):
            self._req("POST", f"/channels/{channel}/messages",
                      {"content": text[i:i + 1990]})


def fmt_event(start, end) -> str:
    if start.date() == end.date():
        return f"{start:%a %b %d %I:%M %p} - {end:%I:%M %p}"
    return f"{start:%a %b %d %I:%M %p} - {end:%a %b %d %I:%M %p}"


class CalendarSession:
    """Per-DM conversation state machine. handle() -> reply text."""

    def __init__(self, cfg: dict, llm: LLM):
        self.cfg = cfg
        self.llm = llm
        self.state = None  # (kind, payload)

    def _resolve_when(self, parsed) -> dates.Resolved:
        return dates.resolve(parsed.get("when") or "", "",
                             parsed.get("duration") or "",
                             default_duration_min=self.cfg["default_duration_min"])

    def handle(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        low = text.lower()
        if low in ("never", "no auto"):
            self.cfg["auto_confirm"] = False
            return "confirmations on."
        if low in ("always", "auto"):
            self.cfg["auto_confirm"] = True
            return "auto-adding without confirm (say 'never' to undo)."

        kind = self.state[0] if self.state else None
        payload = self.state[1] if self.state else None

        if kind == "add_title":
            payload["title"] = text
            self.state = None
            return self.handle_add(payload)
        if kind == "add_when":
            prev = payload[1]
            if low in ("ok", "yes", "y", "fine", "sure") and prev and prev.ok:
                self.state = ("confirm_add", {"title": payload[0].get("title") or "event",
                                              "start": prev.start, "end": prev.end})
                return f"{self.state[1]['title']} - {fmt_event(prev.start, prev.end)}. add? (y/n)"
            parsed = payload[0]
            parsed["when"] = text
            self.state = None
            return self.handle_add(parsed)
        if kind == "confirm_add":
            if low in ("y", "yes", "add", "yep", "sure", "ok"):
                ev = gcal.create_event(self.cfg, payload["title"],
                                       payload["start"].isoformat(),
                                       payload["end"].isoformat())
                self.state = None
                return f"added: {ev.get('htmlLink', '')}"
            self.state = None
            return "cancelled."
        if kind == "confirm_delete":
            if low in ("y", "yes"):
                gcal.delete_event(self.cfg, payload["id"])
                self.state = None
                return f"deleted: {payload.get('summary')}"
            self.state = None
            return "kept it."

        try:
            parsed = parse_command(self.llm, text)
        except (LLMError, ValueError) as e:
            return f"LLM hiccup: {e}"
        intent = parsed.get("intent")
        if intent == "add":
            return self.handle_add(parsed)
        if intent == "list":
            return self.handle_list(parsed.get("when") or "")
        if intent == "delete":
            return self.handle_delete(parsed.get("target") or "")
        if intent == "reschedule":
            return "reschedule by DM is coming; for now: delete + re-add."
        if intent == "help":
            return ("DM me things like 'add dentist tmr at 3', "
                    "'what do i have this weekend', 'cancel dentist'.")
        return "didn't catch that — try 'add ...' or 'what do i have ...'"

    def handle_add(self, parsed) -> str:
        title = (parsed.get("title") or "").strip()
        if not title:
            self.state = ("add_title", parsed)
            return "what's the event called?"
        r = self._resolve_when(parsed)
        if r.ambiguous and not r.ok:
            self.state = ("add_when", (parsed, None))
            return "when is it? (e.g. 'tmr 3pm', 'nov 20 noon')"
        if r.ambiguous:  # has a start but defaulted something
            self.state = ("add_when", (parsed, r))
            return f"i read {fmt_event(r.start, r.end)} — {r.notes[0] if r.notes else 'sure?'} (type a fix or 'ok')"
        if self.cfg["auto_confirm"]:
            ev = gcal.create_event(self.cfg, title, r.start.isoformat(), r.end.isoformat())
            return f"added: {title} - {fmt_event(r.start, r.end)}\n{ev.get('htmlLink', '')}"
        self.state = ("confirm_add", {"title": title, "start": r.start, "end": r.end})
        return f"{title} - {fmt_event(r.start, r.end)}. add? (y/n)"

    def handle_list(self, when: str) -> str:
        now = dates.datetime.now()
        r = dates.resolve(when, "", "") if when else None
        if r and r.ok:
            tmin, tmax, label = r.start, r.start + dates.timedelta(days=1), f"{r.start:%a %b %d}"
        else:
            tmin, tmax, label = now, now + dates.timedelta(days=7), "next 7 days"
        events = gcal.list_events(self.cfg, tmin.isoformat(), tmax.isoformat())
        if not events:
            return f"nothing on {label}."
        lines = [f"{label}:"]
        for ev in events[:15]:
            st = ev.get("start", {})
            when_s = st.get("dateTime", st.get("date", "?"))[:16].replace("T", " ")
            lines.append(f"  {when_s}  {ev.get('summary', '(no title)')}")
        return "\n".join(lines)

    def handle_delete(self, target: str) -> str:
        if not target:
            return "which event?"
        now = dates.datetime.now()
        events = gcal.list_events(self.cfg, now.isoformat(),
                                  (now + dates.timedelta(days=21)).isoformat())
        matches = [e for e in events if target.lower() in (e.get("summary") or "").lower()]
        if not matches:
            return f"no upcoming event matching '{target}'."
        ev = matches[0]
        self.state = ("confirm_delete", ev)
        extra = f" (+{len(matches)-1} more match — say y for the first only)" if len(matches) > 1 else ""
        return f"delete '{ev.get('summary')}'? (y/n){extra}"


def run_bot(cfg: dict, token: str, allowed: list[str], poll_secs: float = 2.0) -> None:
    llm = LLM(cfg["endpoint"], cfg["model"])
    dc = Discord(token)
    me = dc.whoami()
    print(f"yapcal bot: {me.get('username')}#{me.get('discriminator')} "
          f"| model={llm.pick_model()} | allowed_ids={allowed or 'ANYONE (set --allow!)'}")
    seen: dict[str, str] = {}   # channel -> last message id
    sessions: dict[str, CalendarSession] = {}
    while True:
        try:
            for ch in dc.dm_channels():
                cid = ch["id"]
                after = seen.get(cid)
                if after is None:
                    msgs = dc.messages(cid, "")
                    seen[cid] = msgs[0]["id"] if msgs else ""
                    continue
                new = dc.messages(cid, after)
                if not new:
                    continue
                seen[cid] = new[0]["id"]
                for msg in reversed(new):
                    uid = msg.get("author", {}).get("id")
                    # skip other bots unless explicitly allowlisted (test harness)
                    if msg.get("author", {}).get("bot") and uid not in allowed:
                        continue
                    if allowed and uid not in allowed:
                        continue
                    sess = sessions.setdefault(cid, CalendarSession(cfg, llm))
                    try:
                        reply = sess.handle(msg.get("content", ""))
                    except gcal.GCalError as e:
                        reply = f"calendar error: {e}"
                    if reply:
                        dc.send(cid, reply)
        except DiscordError as e:
            print(f"discord error: {e}")
        except KeyboardInterrupt:
            return
        time.sleep(poll_secs)
