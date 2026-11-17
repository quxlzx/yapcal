"""Chat-mode intent extraction. The LLM classifies + extracts raw phrases;
dates.py resolves them deterministically."""
from __future__ import annotations

from .llm import LLM

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["add", "list", "delete", "reschedule", "help", "other"]},
        "title": {"type": ["string", "null"]},
        "when": {"type": ["string", "null"]},
        "duration": {"type": ["string", "null"]},
        "target": {"type": ["string", "null"]},
    },
    "required": ["intent", "title", "when", "duration", "target"],
}

SYSTEM = """You parse short calendar commands into JSON. Rules:
- "add" = create event. Put the event name in title. Put any date/time words in when (copy them exactly, e.g. "tmr", "friday at 3pm"). Put length words in duration (e.g. "2 hours").
- "list" = show events. Put the range words in when (e.g. "this weekend").
- "delete" = remove event. Put which event in target.
- "reschedule" = move event. Put which event in target and the new time in when.
- Never invent dates. Copy the user's own words. No time -> when can omit time.
Examples:
"add dentist tmr" -> {"intent":"add","title":"dentist","when":"tmr","duration":null,"target":null}
"gym on friday at 6 for 90 min" -> {"intent":"add","title":"gym","when":"friday at 6","duration":"90 min","target":null}
"what do i have tomorrow" -> {"intent":"list","title":null,"when":"tomorrow","duration":null,"target":null}
"cancel the dentist thing" -> {"intent":"delete","title":null,"when":null,"duration":null,"target":"dentist"}
"move gym to next monday 8am" -> {"intent":"reschedule","title":null,"when":"next monday 8am","duration":null,"target":"gym"}
"""


def parse_command(llm: LLM, text: str) -> dict:
    out = llm.chat_json(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": text}],
        schema=INTENT_SCHEMA)
    if not isinstance(out, dict) or "intent" not in out:
        raise ValueError(f"bad model output: {out}")
    return out
