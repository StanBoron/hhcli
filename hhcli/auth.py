# hhcli/auth.py
from __future__ import annotations

import time
from typing import Any

import requests

from hhcli.config import load_config, save_config

# Базовые адреса
AUTH_BASE = "https://hh.ru"
API_BASE = "https://api.hh.ru"


def build_oauth_url(scope: str = "read+resumes+negotiations") -> str:
    """
    Сформировать ссылку на авторизацию (authorization_code).
    После логина hh.ru сделает редирект на redirect_uri?code=...
    """
    cfg = load_config()
    client_id = cfg.get("client_id") or ""
    redirect_uri = cfg.get("redirect_uri") or "http://localhost:8501"
    if not client_id:
        raise RuntimeError("client_id не задан. Выполните: hhcli config --client-id ...")

    return (
        f"{AUTH_BASE}/oauth/authorize"
        f"?response_type=code&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope}"
    )


def _token_headers() -> dict[str, str]:
    cfg = load_config()
    ua = cfg.get("user_agent") or "hhcli/0.1"
    return {"User-Agent": ua, "Accept": "application/json"}


def exchange_code(code: str) -> dict[str, Any]:
    """
    Обменять authorization code на access/refresh токены.
    """
    cfg = load_config()
    data = {
        "grant_type": "authorization_code",
        "client_id": cfg.get("client_id", ""),
        "client_secret": cfg.get("client_secret", ""),
        "redirect_uri": cfg.get("redirect_uri", "http://localhost:8501"),
        "code": code,
    }
    resp = requests.post(f"{API_BASE}/token", data=data, headers=_token_headers(), timeout=30)
    resp.raise_for_status()
    tk = resp.json()

    # сохранить токены
    cfg["access_token"] = tk.get("access_token", "")
    # hh иногда возвращает новый refresh_token — сохраним если есть
    cfg["refresh_token"] = tk.get("refresh_token", cfg.get("refresh_token", ""))
    # expires_in (сек) → UNIX-время истечения
    expires_in = int(tk.get("expires_in") or 0)
    cfg["token_expires_at"] = int(time.time()) + expires_in

    save_config(cfg)
    return tk


def refresh_access_token() -> dict[str, Any]:
    """
    Обновить access_token по refresh_token.
    """
    cfg = load_config()
    if not cfg.get("refresh_token"):
        raise RuntimeError(
            "Нет refresh_token. Пройдите авторизацию через oauth-url / oauth-exchange."
        )

    data = {
        "grant_type": "refresh_token",
        "client_id": cfg.get("client_id", ""),
        "client_secret": cfg.get("client_secret", ""),
        "refresh_token": cfg.get("refresh_token", ""),
    }
    resp = requests.post(f"{API_BASE}/token", data=data, headers=_token_headers(), timeout=30)
    resp.raise_for_status()
    tk = resp.json()

    cfg["access_token"] = tk.get("access_token", "")
    # Иногда приходит новый refresh_token — обновим, если есть
    if tk.get("refresh_token"):
        cfg["refresh_token"] = tk["refresh_token"]
    expires_in = int(tk.get("expires_in") or 0)
    cfg["token_expires_at"] = int(time.time()) + expires_in

    save_config(cfg)
    return tk


# --- Ручная установка токенов (для импорта/вставки вручную) ---


def set_tokens(
    access_token: str, refresh_token: str | None = None, expires_in: int | None = None
) -> dict[str, Any]:
    """
    Сохранить токены напрямую в конфиг.
    Если передан expires_in (сек), вычисляем token_expires_at от текущего времени.
    """
    cfg = load_config()
    cfg["access_token"] = (access_token or "").strip()
    if refresh_token is not None:
        cfg["refresh_token"] = (refresh_token or "").strip()
    if expires_in is not None:
        cfg["token_expires_at"] = int(time.time()) + int(expires_in)
    save_config(cfg)
    return cfg
