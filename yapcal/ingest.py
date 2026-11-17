"""Document ingestion: syllabus/email -> candidate events.

datescan finds date-bearing windows; the LLM labels them (title + kind);
dates.resolve turns raw phrases into real datetimes. Anything unresolvable
is dropped or flagged, never guessed.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import dates, datescan
from .llm import LLM

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "when": {"type": "string"},
                    "duration": {"type": ["string", "null"]},
                    "kind": {"type": "string",
                             "enum": ["deadline", "exam", "assignment", "meeting", "other"]},
                },
                "required": ["title", "when", "kind"],
            },
        }
    },
    "required": ["events"],
}

LABEL_SYSTEM = """You read a short excerpt from a syllabus or email and list dated events in it as JSON.
For each dated event: title = short clear name (include course if visible, e.g. "EN.445 PS3 due"),
when = the date/time words copied EXACTLY from the text, kind = deadline|exam|assignment|meeting|other.
Only include events that have a date in the excerpt. Do not invent dates. If none, return {"events":[]}.
"""


def ingest_text(text: str, llm: LLM, now: datetime | None = None,
                default_duration_min: int = 60, max_events: int = 40) -> list[dict]:
    now = now or datetime.now()
    windows = datescan.scan(text)
    candidates = []
    errors = []
    seen = set()
    for w in windows:
        try:
            out = llm.chat_json(
                [{"role": "system", "content": LABEL_SYSTEM},
                 {"role": "user", "content": w["lines"]}],
                schema=CANDIDATE_SCHEMA)
        except Exception as e:
            errors.append(str(e))
            continue
        events = out.get("events", []) if isinstance(out, dict) else []
        for ev in events:
            if not isinstance(ev, dict) or not ev.get("title") or not ev.get("when"):
                continue
            r = dates.resolve(str(ev["when"]), "", str(ev.get("duration") or ""),
                              now=now, default_duration_min=default_duration_min)
            if not r.ok:
                continue
            if r.start < now - timedelta(hours=12):
                continue  # dated event already passed (e.g. earlier in the syllabus)
            key = (ev["title"].lower().strip(), r.start.date().isoformat())
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "title": ev["title"].strip(),
                "kind": ev.get("kind", "other"),
                "start": r.start, "end": r.end,
                "all_day": ev.get("kind") in ("deadline", "assignment"),
                "raw_when": ev["when"],
                "notes": r.notes,
            })
            if len(candidates) >= max_events:
                return candidates
    if errors and not candidates:
        raise RuntimeError("all label calls failed: " + errors[0][:200])
    candidates.sort(key=lambda c: c["start"])
    return candidates
