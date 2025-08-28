from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.path.expanduser("~")) / ".hhcli" / "config.json"

DEFAULTS: dict[str, Any] = {
    "client_id": "",
    "client_secret": "",
    "redirect_uri": "http://localhost:8501",
    "access_token": "",
    "refresh_token": "",
    "token_expires_at": 0,
    "user_agent": "hhcli/0.1 (+https://example.local)",
}


def ensure_config_dir() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict[str, Any]:
    ensure_config_dir()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    merged = {**DEFAULTS, **data}
    # ENV overlay (приоритетнее файла)
    env_overlay = {
        "client_id": os.getenv("HH_CLIENT_ID") or merged["client_id"],
        "client_secret": os.getenv("HH_CLIENT_SECRET") or merged["client_secret"],
        "redirect_uri": os.getenv("HH_REDIRECT_URI") or merged["redirect_uri"],
        "user_agent": os.getenv("HH_USER_AGENT") or merged["user_agent"],
        "access_token": os.getenv("HH_ACCESS_TOKEN") or merged["access_token"],
        "refresh_token": os.getenv("HH_REFRESH_TOKEN") or merged["refresh_token"],
    }
    merged.update(env_overlay)
    return merged


def save_config(cfg: dict[str, Any]) -> None:
    ensure_config_dir()
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_user_agent() -> str:
    return load_config().get("user_agent", DEFAULTS["user_agent"]) or DEFAULTS["user_agent"]


def get_access_token() -> str:
    return load_config().get("access_token", "")
