"""yapcal CLI entry point: python -m yapcal <command>"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from . import gcal
from .app import run_chat, do_ingest
from .config import load_config, save_config, load_token
from .llm import LLM, LLMError


def main(argv=None):
    cfg = load_config()
    ap = argparse.ArgumentParser(prog="yapcal",
        description="Yap at your calendar. Local LLM + Google Calendar, zero pip deps.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("auth", help="one-time Google OAuth")
    p.add_argument("--client-secret", help="path to Google OAuth client JSON (Desktop app)")
    p.add_argument("--manual", action="store_true", help="skip local callback listener")

    sub.add_parser("chat", help="interactive mode (default)")
    sub.add_parser("doctor", help="check LLM endpoint + calendar auth")

    p = sub.add_parser("parse", help="dry-run: parse one command, no writes")
    p.add_argument("text")

    p = sub.add_parser("ingest", help="add dated events from a syllabus/email file")
    p.add_argument("path")
    p.add_argument("--dry", action="store_true", help="show candidates, add nothing")

    p = sub.add_parser("config", help="set config values (endpoint=... model=...)")
    p.add_argument("pairs", nargs="*")

    args = ap.parse_args(argv)

    if args.cmd == "auth":
        if args.client_secret:
            cfg["client_secret"] = args.client_secret
            save_config(cfg)
        gcal.auth(cfg, manual=args.manual)
        return 0
    if args.cmd == "config":
        for kv in args.pairs:
            k, _, v = kv.partition("=")
            if k == "auto_confirm":
                v = v.lower() in ("1", "true", "yes")
            cfg[k] = v if v != "null" else None
        save_config(cfg)
        print("saved:", {k: cfg[k] for k in ("endpoint", "model", "client_secret",
                                             "auto_confirm", "default_duration_min")})
        return 0
    if args.cmd == "doctor":
        ok = True
        try:
            llm = LLM(cfg["endpoint"], cfg["model"])
            print(f"LLM  ✓ {llm.pick_model()} @ {cfg['endpoint']}")
        except LLMError as e:
            ok = False
            print(f"LLM  ✗ {e}")
        tok = load_token()
        if not tok:
            print("auth ✗ not authorized — run: yapcal auth")
            ok = False
        else:
            try:
                gcal.access_token(cfg)
                print("auth ✓ token valid")
            except gcal.GCalError as e:
                ok = False
                print(f"auth ✗ {e}")
        return 0 if ok else 1
    if args.cmd == "parse":
        from .parse import parse_command
        from . import dates
        llm = LLM(cfg["endpoint"], cfg["model"])
        parsed = parse_command(llm, args.text)
        print("intent:", parsed)
        if parsed.get("when"):
            r = dates.resolve(parsed["when"], "", parsed.get("duration") or "",
                              default_duration_min=cfg["default_duration_min"])
            print("resolved:", r.start if r.ok else "AMBIGUOUS", r.notes)
        return 0
    if args.cmd == "ingest":
        llm = LLM(cfg["endpoint"], cfg["model"])
        do_ingest(cfg, llm, args.path, dry=args.dry)
        return 0
    # default: chat
    run_chat(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
