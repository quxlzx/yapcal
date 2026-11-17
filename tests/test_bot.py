"""Offline test of the bot state machine (mock LLM + mock calendar)."""
import sys
from datetime import datetime

sys.path.insert(0, ".")
from yapcal import gcal
from yapcal import discordbot

created = []
gcal.create_event = lambda cfg, summary, start, end, **kw: created.append(
    {"summary": summary, "start": start, "end": end}) or {"htmlLink": "link"}
gcal.delete_event = lambda cfg, eid: None
gcal.list_events = lambda cfg, a, b: [{"id": "e1", "summary": "dentist",
                                       "start": {"dateTime": "2026-11-20T15:00:00"}}]

class MockLLM:
    def __init__(self): self.queue = []
    def pick_model(self): return "mock"
    def chat_json(self, msgs, schema=None): return self.queue.pop(0)

llm = MockLLM()
cfg = {"default_duration_min": 60, "auto_confirm": False, "endpoint": "", "model": ""}
s = discordbot.CalendarSession(cfg, llm)
fails = 0

def expect(reply, needle, label):
    global fails
    if needle not in reply:
        print(f"FAIL {label}: {reply!r} lacks {needle!r}"); fails += 1
    else:
        print(f"ok   {label}: {reply!r}")

# flow 1: add with default time -> ok -> y
llm.queue.append({"intent": "add", "title": "gym", "when": "friday", "duration": None, "target": None})
expect(s.handle("add gym friday"), "defaulted", "default-time question")
expect(s.handle("ok"), "add? (y/n)", "ok -> confirm")
expect(s.handle("y"), "added", "y -> created")
assert created and created[0]["summary"] == "gym", created
print("ok   event created:", created[0]["start"])

# flow 2: add missing title -> answer -> defaulted date -> fix -> n
llm.queue.append({"intent": "add", "title": None, "when": None, "duration": None, "target": None})
expect(s.handle("add something"), "called?", "ask title")
expect(s.handle("dentist"), "assuming today", "title -> confirm default date")
expect(s.handle("tmr 3pm"), "add? (y/n)", "fix date -> confirm")
expect(s.handle("n"), "cancelled", "n -> cancelled")

# flow 3: delete with confirm
llm.queue.append({"intent": "delete", "title": None, "when": None, "duration": None, "target": "dentist"})
expect(s.handle("cancel dentist"), "delete 'dentist'? (y/n)", "delete confirm")
expect(s.handle("y"), "deleted", "deleted")

# flow 4: list
llm.queue.append({"intent": "list", "title": None, "when": "this week", "duration": None, "target": None})
expect(s.handle("what do i have this week"), "dentist", "list shows event")

# flow 5: auto_confirm
llm.queue.append({"intent": "add", "title": "run", "when": "tmr 7am", "duration": None, "target": None})
expect(s.handle("always"), "auto-adding", "auto on")
expect(s.handle("add run tmr 7am"), "added", "auto add no confirm")

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURES"))
sys.exit(1 if fails else 0)
