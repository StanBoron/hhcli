from __future__ import annotations

import time

import requests

from .config import load_config, save_config
from .http import BASE_URL

SCOPES = ["read", "negotiations", "resumes"]


def build_oauth_url() -> str:
    cfg = load_config()
    if not cfg.get("client_id"):
        raise RuntimeError("client_id is empty; set it via CLI")
        scope = "+".join(SCOPES)
        return (
            "https://hh.ru/oauth/authorize"
            f"?response_type=code&client_id={cfg['client_id']}"
            f"&redirect_uri={cfg['redirect_uri']}"
            f"&scope={scope}"
        )


def exchange_code(code: str) -> None:
    cfg = load_config()
    data = {
        "grant_type": "authorization_code",
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": cfg["redirect_uri"],
        "code": code,
    }
    resp = requests.post(
        f"{BASE_URL}/token",
        data=data,
        timeout=30,
        headers={"User-Agent": cfg.get("user_agent", "hhcli/0.1"), "Accept": "application/json"},
    )
    resp.raise_for_status()
    tk = resp.json()
    cfg["access_token"] = tk.get("access_token", "")
    cfg["refresh_token"] = tk.get("refresh_token", "")
    cfg["token_expires_at"] = int(time.time()) + int(tk.get("expires_in", 0))
    save_config(cfg)


def refresh_token() -> None:
    cfg = load_config()
    if not cfg.get("refresh_token"):
        raise RuntimeError("refresh_token is empty; run oauth-exchange with a code first")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": cfg["refresh_token"],
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        }
    resp = requests.post(
        f"{BASE_URL}/token",
        data=data,
        timeout=30,
        headers={"User-Agent": cfg.get("user_agent", "hhcli/0.1"), "Accept": "application/json"},
    )
    resp.raise_for_status()
    tk = resp.json()
    cfg["access_token"] = tk.get("access_token", "")
    cfg["token_expires_at"] = int(time.time()) + int(tk.get("expires_in", 0))
    save_config(cfg)
