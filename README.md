# yapcal

**Yap at your calendar. It handles the details.**

A tiny terminal app that talks to your Google Calendar using a **local LLM**.
No cloud AI APIs, no paid keys, and **zero pip dependencies** — pure Python
stdlib. If it runs `python3`, it runs yapcal.

```
> add dentist tmr at 3
  📅 dentist — Tue Nov 17, 3:00PM – 4:00PM
  add? [Y/n] y
  ✓ added: https://calendar.google.com/calendar/event?eid=...

> what do i have this weekend
  Sat Nov 21: 2 event(s)
   · 2026-11-21 10:00  gym
   · 2026-11-21 19:00  dinner w/ friends

> ingest syllabus.pdf --dry
  found 11 candidate(s):
    1. 📚 EN.445 PS3 due — Fri Nov 20, 9:00AM [all-day]
    2. 📝 EN.445 midterm — Mon Nov 30, 9:00AM
   ...
  add which? [all / 1,3,5 / q]:
```

## Discord bot mode

DM a bot on your account and it manages your calendar with the same
parser + local LLM. Stdlib-only (REST long-poll, no websockets, no pip):

```bash
# discord.com/developers/applications -> New App -> Bot -> Reset Token
python3 -m yapcal bot --token <BOT_TOKEN> --allow <YOUR_DISCORD_USER_ID>
```

Only allowlisted user IDs can touch your calendar. Multi-turn works:
missing times get follow-up questions, every add/delete needs a `y`.

## Which model? (benchmarked)

`bench/` contains a 100-case extraction benchmark (intent + slot copying,
schema-locked JSON). Results for 16 models under ~4B (`bench/results.csv`),
run on an RTX 5060 Laptop:

| model | score/100 | JSON fails | tok/s |
|---|---|---|---|
| **qwen3:1.7b** ← recommended | **88.9** | 0 | 143 |
| qwen3:4b | 88.8 | 0 | 78 |
| llama3.2:3b | 88.4 | 0 | 103 |
| phi3:mini | 88.2 | 0 | 99 |
| qwen3:0.6b | 79.9 | 0 | 161 |
| deepseek-r1:1.5b | 60.4 | 13 | — |

Takeaways: the quality knee is at **1.7B** (4B buys nothing at 2x the cost);
schema-constrained decoding gives ~0 format failures even at 360M; reasoning
models are the wrong tool for extraction.

## Why it's reliable with small models

The LLM **never does date math**. It only classifies intent and copies raw
phrases (`"tmr"`, `"nov 20 11:59pm"`); a deterministic parser resolves them
against your clock. For documents (syllabi, emails), regex finds the
date-bearing lines and the LLM just *names* the events in each window.
Schema-constrained JSON (llama.cpp / Ollama) keeps even a 1.7B model honest.

## Setup (one-time, ~5 minutes, all free)

1. **LLM** — run any OpenAI-compatible server:
   ```bash
   ollama pull qwen3:1.7b && ollama serve        # or
   llama-server -m Qwen3-1.7B-Q4_K_M.gguf --port 8080   # llama.cpp
   ```
   Windows: `winget install Ollama.Ollama`, then `ollama pull qwen3:1.7b`.
   yapcal auto-detects the model; point it elsewhere with:
   ```bash
   python3 -m yapcal config endpoint=http://127.0.0.1:8080/v1 model=qwen3
   ```

2. **Google Calendar** — one free OAuth client (this is *not* an API key
   purchase; it's a Google Cloud "Desktop app" credential):
   - [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials)
     → Create Credentials → OAuth client ID → **Desktop app** → download JSON
   - If the app is in "Testing", add your account under
     [Audience → Test users](https://console.cloud.google.com/auth/audience)
   ```bash
   python3 -m yapcal auth --client-secret ~/Downloads/client_secret_xxx.json
   ```
   A browser opens; approve once. The token is stored in
   `~/.config/yapcal/token.json` and auto-refreshes forever after.

3. **Sanity check:** `python3 -m yapcal doctor`

## Usage

```bash
python3 -m yapcal                 # interactive chat
python3 -m yapcal today           # quick agenda
python3 -m yapcal ingest syllabus.pdf          # bulk-add dated events
python3 -m yapcal ingest email.txt --dry       # preview only
python3 -m yapcal parse "move gym to friday 6" # dry-run one command
```

Supported phrasing: `tmr`, `next friday`, `in 3 days`, `this weekend`,
`nov 20`, `11/20`, `the 20th`, `3pm`, `15:30`, `noon`, `tonight`,
`for 90 min`… Anything ambiguous gets a clarifying question instead of a
guess. Missing time → it asks. Missing title → it asks.

## Design

| module | job |
|---|---|
| `dates.py` | deterministic date/time resolution (24 unit tests) |
| `llm.py` | OpenAI-compatible client, JSON-schema mode + fallback |
| `parse.py` | chat intent extraction (add/list/delete/reschedule) |
| `datescan.py` | regex scan of documents for dated lines |
| `ingest.py` | windowed LLM labeling → candidate events |
| `gcal.py` | OAuth2 PKCE + Calendar REST (stdlib urllib) |
| `app.py` | REPL, slot-filling, batch confirm |

Nothing is ever written to your calendar without a confirmation (flip
`auto_confirm` if you're feeling brave).

## Requirements

Python 3.10+ on any OS. PDF ingestion uses `pdftotext` if installed
(optional); `.txt/.md/.eml/.docx` work out of the box.

## Tests

```bash
python3 tests/test_dates.py
```
