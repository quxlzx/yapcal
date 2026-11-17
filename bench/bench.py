"""yapcal model benchmark: 100 calendar-command extraction cases against
any Ollama-served model. Stdlib only. Run on the machine hosting Ollama.

Usage: py bench.py [model ...]     (default: MODELS list below)
Writes bench_results.jsonl (per-case) + bench_summary.csv (per-model).
"""
import csv
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from yapcal.parse import SYSTEM, INTENT_SCHEMA

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = json.load(open(os.path.join(HERE, "cases.json")))

MODELS = [
    "qwen3:0.6b", "qwen3:1.7b", "qwen3:4b", "qwen3:4b-instruct-2507",
    "llama3.2:1b", "llama3.2:3b",
    "phi4-mini:3.8b", "phi3:mini",
    "gemma3:1b", "gemma3:4b",
    "qwen2.5:1.5b", "qwen2.5:3b",
    "smollm2:1.7b", "smollm2:360m",
    "tinyllama:1.1b",
    "deepseek-r1:1.5b", "deepseek-r1:3.5b",
    "exaone3.5:2.5b",
]

NORM = lambda s: re.sub(r"[^a-z0-9/ ]+", " ", (s or "").lower())


def norm_json(v):
    """Coerce model output to the expected dict shape."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return None
    return v if isinstance(v, dict) else None


def kw_hit(model_text, kws):
    """All keywords present (any-of for multi-word alternatives separated by |)."""
    mt = " " + NORM(model_text) + " "
    return all(any(alt.strip() in mt for alt in kw.split("|")) for kw in kws)


def score_case(case, out):
    """0..1 score. intent .4 / when .3 / title .2 / target+duration .1"""
    o = norm_json(out)
    if not o:
        return 0.0, "json_fail"
    pts, notes = 0.0, []
    if o.get("intent") == case["intent"]:
        pts += 0.4
    else:
        notes.append(f"intent={o.get('intent')}")
    # when
    mw = o.get("when")
    if not case["when_kw"]:
        pts += 0.3 if not mw else 0.15  # should be null; extra when is sloppy
    elif kw_hit(mw, case["when_kw"]):
        pts += 0.3
    else:
        notes.append(f"when={mw!r}")
    # title (add) / target (delete,reschedule)
    if case["title_kw"]:
        if kw_hit(o.get("title"), case["title_kw"]):
            pts += 0.2
        elif o.get("title"):
            pts += 0.05
            notes.append(f"title={o.get('title')!r}")
        else:
            notes.append("title=null")
    if case["target_kw"]:
        pts += 0.1 if kw_hit(o.get("target"), case["target_kw"]) else 0.0
        if not kw_hit(o.get("target"), case["target_kw"]):
            notes.append(f"target={o.get('target')!r}")
    if case["duration_kw"]:
        # duration shares the last 0.1 when no target, else bonus capped at 1
        if kw_hit(o.get("duration"), case["duration_kw"]):
            pts += 0.05
    max_pts = (0.4 + 0.3 + (0.2 if case["title_kw"] else 0)
               + (0.1 if case["target_kw"] else 0) + (0.05 if case["duration_kw"] else 0))
    return min(pts / max_pts, 1.0), ";".join(notes)


def ask(model, text):
    body = {"model": model,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": text}],
            "format": INTENT_SCHEMA, "options": {"temperature": 0, "num_ctx": 2048},
            "stream": False}
    if "qwen3" in model:
        body["think"] = False  # match yapcal production behaviour
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.load(r)
    dt = time.time() - t0
    return d.get("message", {}).get("content", ""), dt, d.get("eval_count", 0)


def run(model):
    results, total, json_fails, tps = [], 0.0, 0, []
    try:
        ask(model, "hi")  # warmup: load model before timing
    except Exception:
        pass
    for i, case in enumerate(CASES):
        try:
            raw, dt, ec = ask(model, case["input"])
            if ec:
                tps.append(ec / dt)
        except Exception as e:
            raw, dt = f"__ERR__ {e}", 0
        s, note = score_case(case, raw)
        if note == "json_fail":
            json_fails += 1
        total += s
        results.append({"model": model, "i": i, "input": case["input"],
                        "score": s, "note": note, "raw": raw[:400]})
        if i % 10 == 0:
            print(f"  {model}: {i+1}/100 score={total:.1f}", flush=True)
    return total, json_fails, results, (sum(tps) / len(tps) if tps else 0)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    models = sys.argv[1:] or MODELS
    print(f"{len(CASES)} cases x {len(models)} models", flush=True)
    with open(HERE + "/bench_summary.csv", "a", newline="") as csvf:
        w = csv.writer(csvf)
        w.writerow(["model", "score_100", "json_fails", "tok_s", "ts"])
        for m in models:
            t0 = time.time()
            total, jf, results, tps = run(m)
            with open(HERE + "/bench_results.jsonl", "a") as jf_:
                for r in results:
                    jf_.write(json.dumps(r, ensure_ascii=False) + "\n")
            w.writerow([m, round(total, 1), jf, round(tps, 1), int(time.time())])
            csvf.flush()
            print(f"DONE {m}: {total:.1f}/100  json_fails={jf}  {tps:.0f} tok/s",
                  flush=True)


if __name__ == "__main__":
    main()
