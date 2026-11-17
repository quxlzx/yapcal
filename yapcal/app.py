"""Interactive REPL: slot-filling chat + batch document ingestion."""
from __future__ import annotations

import difflib
from datetime import datetime, timedelta

from . import dates, gcal, ingest
from .llm import LLM, LLMError
from .parse import parse_command


def fmt_dt(start: datetime, end: datetime) -> str:
    s = f"{start:%a %b %d, %I:%M%p}"
    e = f"{end:%I:%M%p}"
    return f"{s} – {e}" if start.date() == end.date() else f"{s} – {end:%a %b %d, %I:%M%p}"


def _ask(when_hint: str) -> str:
    return input(f"  ? {when_hint} (or Enter to skip): ").strip()


def do_add(cfg: dict, llm: LLM, parsed: dict) -> None:
    title = (parsed.get("title") or "").strip()
    if not title:
        title = _ask("what's the event called?")
        if not title:
            print("  cancelled — no title.")
            return
    r = dates.resolve(parsed.get("when") or "", "", parsed.get("duration") or "",
                      default_duration_min=cfg["default_duration_min"])
    while r.ambiguous:
        for n in r.notes:
            print(f"  · {n}")
        fix = _ask("when is it? (e.g. 'tmr 3pm', 'nov 20 noon')")
        if not fix:
            print("  cancelled — time unclear.")
            return
        r = dates.resolve(fix, "", parsed.get("duration") or "",
                          default_duration_min=cfg["default_duration_min"])
    print(f"  📅 {title} — {fmt_dt(r.start, r.end)}")
    if not cfg["auto_confirm"] and input("  add? [Y/n] ").strip().lower() in ("n", "no"):
        print("  cancelled.")
        return
    ev = gcal.create_event(cfg, title, r.start.isoformat(), r.end.isoformat())
    print(f"  ✓ added: {ev.get('htmlLink', ev.get('id'))}")


def do_list(cfg: dict, when: str) -> None:
    now = datetime.now()
    r = dates.resolve(when, "", "") if when else None
    if when and r and not r.ok:
        print("  couldn't understand that range; showing next 7 days.")
        r = None
    if r and r.ok:
        tmin, tmax = r.start, r.start + timedelta(days=1)
        label = f"{r.start:%a %b %d}"
    else:
        tmin, tmax = now, now + timedelta(days=7)
        label = "next 7 days"
    events = gcal.list_events(cfg, tmin.astimezone().isoformat(), tmax.astimezone().isoformat())
    print(f"  {label}: {len(events)} event(s)")
    for ev in events:
        st = ev.get("start", {})
        when_s = st.get("dateTime", st.get("date", "?"))[:16].replace("T", " ")
        print(f"   · {when_s}  {ev.get('summary', '(no title)')}")


def do_delete(cfg: dict, target: str) -> None:
    now = datetime.now()
    events = gcal.list_events(cfg, now.isoformat(), (now + timedelta(days=21)).isoformat())
    matches = [e for e in events if target.lower() in (e.get("summary") or "").lower()]
    if not matches:
        names = difflib.get_close_matches(target.lower(),
                                          [(e.get("summary") or "").lower() for e in events], 3)
        print(f"  no match for '{target}'." + (f" did you mean: {names}?" if names else ""))
        return
    for ev in matches[:5]:
        print(f"   · {ev.get('summary')} ({ev.get('start', {}).get('dateTime', '')[:16]})")
    if len(matches) > 1 and input(f"  {len(matches)} matches — delete all? [y/N] ").strip().lower() != "y":
        print("  cancelled.")
        return
    gcal.delete_event(cfg, matches[0]["id"])
    print(f"  ✓ deleted: {matches[0].get('summary')}")


def do_reschedule(cfg: dict, parsed: dict) -> None:
    target = (parsed.get("target") or "").strip()
    if not target:
        target = _ask("which event?")
    now = datetime.now()
    events = gcal.list_events(cfg, now.isoformat(), (now + timedelta(days=30)).isoformat())
    matches = [e for e in events if target.lower() in (e.get("summary") or "").lower()]
    if not matches:
        print(f"  no upcoming event matching '{target}'.")
        return
    old = matches[0]
    r = dates.resolve(parsed.get("when") or "", "", "")
    while r.ambiguous:
        for n in r.notes:
            print(f"  · {n}")
        fix = _ask("move it to when?")
        if not fix:
            print("  cancelled.")
            return
        r = dates.resolve(fix, "", "")
    print(f"  ↻ {old.get('summary')} → {fmt_dt(r.start, r.end)}")
    if input("  confirm (recreates the event at the new time)? [y/N] ").strip().lower() != "y":
        print("  cancelled.")
        return
    gcal.delete_event(cfg, old["id"])
    ev = gcal.create_event(cfg, old.get("summary", ""), r.start.isoformat(), r.end.isoformat())
    print(f"  ✓ moved: {ev.get('htmlLink', ev.get('id'))}")


def do_ingest(cfg: dict, llm: LLM, path: str, dry: bool = False) -> None:
    text = ingest.datescan.load_document(path)
    print(f"  scanning {path} ({len(text)} chars)...")
    cands = ingest.ingest_text(text, llm,
                               default_duration_min=cfg["default_duration_min"])
    if not cands:
        print("  found no dated events. (Try pasting the text into a .txt file.)")
        return
    print(f"  found {len(cands)} candidate(s):")
    for i, c in enumerate(cands, 1):
        tag = {"deadline": "⏰", "exam": "📝", "assignment": "📚",
               "meeting": "🤝"}.get(c["kind"], "📌")
        print(f"   {i:2d}. {tag} {c['title']} — {fmt_dt(c['start'], c['end'])}"
              + (" [all-day]" if c["all_day"] else ""))
    if dry:
        print("  (dry run — nothing added)")
        return
    choice = input("add which? [all / 1,3,5 / q]: ").strip().lower()
    if choice in ("q", "quit", "none", ""):
        print("  cancelled.")
        return
    if choice == "all":
        picks = cands
    else:
        try:
            picks = [cands[int(n) - 1] for n in choice.replace(" ", "").split(",")]
        except ValueError:
            print("  didn't understand; cancelled.")
            return
    added = 0
    for c in picks:
        try:
            if c["all_day"]:
                ev = gcal.create_event(cfg, c["title"], c["start"].date().isoformat(),
                                       (c["start"] + timedelta(days=1)).date().isoformat(),
                                       all_day=True)
            else:
                ev = gcal.create_event(cfg, c["title"], c["start"].isoformat(),
                                       c["end"].isoformat())
            added += 1
            print(f"  ✓ {c['title']}")
        except gcal.GCalError as e:
            print(f"  ✗ {c['title']}: {e}")
    print(f"  done — {added}/{len(picks)} added.")


def run_chat(cfg: dict) -> None:
    llm = LLM(cfg["endpoint"], cfg["model"])
    print(f"yapcal — talking to {cfg['endpoint']} ({llm.pick_model()})")
    print("type calendar commands, or: today | week | ingest <file> | quit\n")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        low = text.lower()
        if low in ("quit", "exit", "q"):
            return
        try:
            if low in ("today", "tmr", "tomorrow", "week", "this week"):
                do_list(cfg, "" if low in ("week", "this week") else low)
                continue
            if low.startswith("ingest "):
                do_ingest(cfg, llm, text[7:].strip())
                continue
            parsed = parse_command(llm, text)
            intent = parsed["intent"]
            if intent == "add":
                do_add(cfg, llm, parsed)
            elif intent == "list":
                do_list(cfg, parsed.get("when") or "")
            elif intent == "delete":
                do_delete(cfg, parsed.get("target") or "")
            elif intent == "reschedule":
                do_reschedule(cfg, parsed)
            elif intent == "help":
                print("  examples: 'add dentist tmr 3pm' · 'what do i have friday' · "
                      "'cancel dentist' · 'move gym to monday 8am' · 'ingest syllabus.pdf'")
            else:
                print("  didn't catch that — try 'add ...' or 'what do i have ...'")
        except LLMError as e:
            print(f"  LLM error: {e}")
        except gcal.GCalError as e:
            print(f"  calendar error: {e}")
        except KeyboardInterrupt:
            print("\n  (cancelled)")
