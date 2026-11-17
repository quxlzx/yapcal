"""Deterministic relative-date resolution. The LLM never does date math —
it extracts raw phrases ('tmr', '3pm') and this module resolves them.

resolve(date_phrase, time_phrase, duration_phrase, now) -> Resolved
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2,
    "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4,
    "friday": 4, "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}
TIME_KEYWORDS = {
    "noon": (12, 0), "midnight": (0, 0), "morning": (9, 0), "afternoon": (14, 0),
    "evening": (19, 0), "tonight": (20, 0), "eod": (17, 0), "end of day": (17, 0),
    "lunch": (12, 0), "brunch": (11, 0),
}
DEFAULT_TIME = (9, 0)


@dataclass
class Resolved:
    start: datetime | None = None
    end: datetime | None = None
    notes: list[str] = field(default_factory=list)
    ambiguous: bool = False       # True -> app should ask the user
    all_day: bool = False

    @property
    def ok(self) -> bool:
        return self.start is not None


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[,\s]+", " ", s)
    s = re.sub(r"^(on|at|by|the|for)\s+", "", s)
    return s


def _next_weekday(base: datetime, wd: int, strictly_next_week: bool = False) -> datetime:
    d = base.date()
    if strictly_next_week:
        # "next friday" = that weekday of the *next* week: jump to next Monday first
        d = d + timedelta(days=(7 - base.weekday()) % 7 or 7)
        d = d + timedelta(days=(wd - d.weekday()) % 7)
    else:
        days_ahead = (wd - d.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # bare weekday today -> next week
        d = d + timedelta(days=days_ahead)
    return datetime(d.year, d.month, d.day)


def _parse_time(t: str, now: datetime) -> tuple[int, int] | None:
    t = _norm(t)
    if not t:
        return None
    if t in TIME_KEYWORDS:
        return TIME_KEYWORDS[t]
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?$", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if mi >= 60:
            return None
        if ap in ("pm", "p"):
            if h < 12:
                h += 12
        elif ap in ("am", "a"):
            if h == 12:
                h = 0
        elif h > 23:
            return None
        # bare number <= 7 with no am/pm and a date context -> likely pm-ish; keep as-is
        if not ap and 1 <= h <= 7:
            h += 12  # "add gym at 4" -> 16:00
        if h <= 23:
            return (h, mi)
    m = re.match(r"^(\d{1,2})[.:](\d{2})$", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h <= 23 and mi < 60:
            return (h, mi)
    return None


def _parse_duration(d: str, default_min: int) -> int:
    d = _norm(d)
    if not d:
        return default_min
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours)$", d)
    if m:
        return int(float(m.group(1)) * 60)
    m = re.match(r"^(\d+)\s*(m|min|mins|minute|minutes)$", d)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)\s*(d|day|days)$", d)
    if m:
        return int(m.group(1)) * 24 * 60  # multi-day event
    m = re.match(r"^(\d+)\s*(w|week|weeks)$", d)
    if m:
        return int(m.group(1)) * 7 * 24 * 60
    return default_min


def _split_date_time(phrase: str) -> tuple[str, str]:
    """Split a combined phrase like 'tmr at 3pm' into (date_part, time_part)."""
    phrase = _norm(phrase)
    m = re.search(r"\s+(?:at|@)\s+(\S+(?:\s?(?:am|pm))?)$", phrase)
    time_part = ""
    if m:
        time_part = m.group(1)
        phrase = phrase[: m.start()]
    return phrase, time_part


def _parse_date(d: str, now: datetime) -> tuple | None:
    """Return (year, month, day) or None."""
    d = _norm(d)
    if not d:
        return None
    if d in ("today", "tod", "tdy"):
        return (now.year, now.month, now.day)
    if d in ("tmr", "tmrw", "tomorow", "tommorow", "tommorrow", "tomorrow", "2mro", "2mrw"):
        t = now + timedelta(days=1)
        return (t.year, t.month, t.day)
    if d == "yesterday":
        t = now - timedelta(days=1)
        return (t.year, t.month, t.day)
    m = re.match(r"^in\s+(\d+)\s*(d|day|days|w|week|weeks)$", d)
    if m:
        n = int(m.group(1))
        mult = 7 if m.group(2).startswith("w") else 1
        t = now + timedelta(days=n * mult)
        return (t.year, t.month, t.day)
    if d in ("next week",):
        t = now + timedelta(days=(7 - now.weekday()) % 7 or 7)
        return (t.year, t.month, t.day)
    if d in ("this weekend", "weekend"):
        t = now + timedelta(days=(5 - now.weekday()) % 7 or 7)
        return (t.year, t.month, t.day)
    m = re.match(r"^next\s+(monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)$", d)
    if m:
        dt = _next_weekday(now, WEEKDAYS[m.group(1)], strictly_next_week=True)
        return (dt.year, dt.month, dt.day)
    if d in WEEKDAYS:
        dt = _next_weekday(now, WEEKDAYS[d])
        return (dt.year, dt.month, dt.day)
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", d)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})[/](\d{1,2})(?:[/](\d{2,4}))?$", d)
    if m:
        mo, dy, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        year = now.year if not yr else (now.year // 100 * 100 + int(yr) if len(yr) == 2 else int(yr))
        return (year, mo, dy)
    m = re.match(r"^(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+(\d{1,2})(?:st|nd|rd|th)?$", d)
    if m:
        mo = MONTHS[m.group(1)]
        year = now.year if mo >= now.month else now.year + 1
        return (year, mo, int(m.group(2)))
    m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)$", d)
    if m:
        mo = MONTHS[m.group(2)]
        year = now.year if mo >= now.month else now.year + 1
        return (year, mo, int(m.group(1)))
    m = re.match(r"^the\s+(\d{1,2})(?:st|nd|rd|th)$", d) or re.match(r"^(\d{1,2})(?:st|nd|rd|th)$", d)
    if m:
        day = int(m.group(1))
        year, mo = now.year, now.month
        try:
            probe = datetime(year, mo, day)
            if probe.date() < now.date():
                mo += 1
                if mo > 12:
                    mo, year = 1, year + 1
            return (year, mo, day)
        except ValueError:
            return None
    return None


def resolve(date_phrase: str = "", time_phrase: str = "", duration_phrase: str = "",
            now: datetime | None = None, default_duration_min: int = 60) -> Resolved:
    now = now or datetime.now()
    res = Resolved()
    dp, tp = _split_date_time(date_phrase) if date_phrase else ("", "")
    if time_phrase:
        tp = time_phrase
    # standalone time-words ("tonight", "noon") imply today
    if dp in TIME_KEYWORDS and not tp:
        tp, dp = dp, "today"
    ymd = _parse_date(dp, now)
    if date_phrase and not ymd:
        res.ambiguous = True
        res.notes.append(f"couldn't parse date: '{dp}'")
        return res
    if not ymd:
        # no date given: today if time still ahead, else tomorrow
        t = _parse_time(tp, now)
        if t and (t[0], t[1]) <= (now.hour, now.minute):
            ymd = (now.year, now.month, now.day)
            res.notes.append("time already passed today — did you mean tomorrow?")
            res.ambiguous = True
        else:
            ymd = (now.year, now.month, now.day)
            res.notes.append("no date given — assuming today")
    try:
        base = datetime(*ymd)
    except ValueError:
        res.ambiguous = True
        res.notes.append(f"invalid date: {ymd}")
        return res

    t = _parse_time(tp, now)
    if t is None and tp:
        res.ambiguous = True
        res.notes.append(f"couldn't parse time: '{tp}'")
        return res
    if t is None:
        t = DEFAULT_TIME
        res.notes.append("no time given — defaulted 9:00 AM")
        res.ambiguous = True
    res.start = base.replace(hour=t[0], minute=t[1])
    dur = _parse_duration(duration_phrase, default_duration_min)
    res.end = res.start + timedelta(minutes=dur)
    if res.start < now - timedelta(hours=12):
        res.notes.append(f"{res.start:%a %b %d} is in the past")
        res.ambiguous = True
    return res
