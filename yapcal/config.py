"""Config + token storage for yapcal. Stdlib only."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("YAPCAL_HOME", Path.home() / ".config" / "yapcal"))
CONFIG_FILE = CONFIG_DIR / "config.json"
TOKEN_FILE = CONFIG_DIR / "token.json"

DEFAULTS = {
    "endpoint": "http://127.0.0.1:11434/v1",
    "model": None,                # auto-detect a chat model if null
    "client_secret": None,        # path to Google OAuth client JSON
    "calendar_id": "primary",
    "default_duration_min": 60,
    "auto_confirm": False,        # True = skip y/n before adding
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except json.JSONDecodeError:
            pass
    # env overrides
    if os.environ.get("YAPCAL_ENDPOINT"):
        cfg["endpoint"] = os.environ["YAPCAL_ENDPOINT"]
    if os.environ.get("YAPCAL_MODEL"):
        cfg["model"] = os.environ["YAPCAL_MODEL"]
    if os.environ.get("YAPCAL_CLIENT_SECRET"):
        cfg["client_secret"] = os.environ["YAPCAL_CLIENT_SECRET"]
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def load_token() -> dict | None:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except json.JSONDecodeError:
            return None
    return None


def save_token(tok: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tok, indent=2))
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
