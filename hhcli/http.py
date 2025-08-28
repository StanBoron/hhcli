from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .config import get_access_token, get_user_agent

logger = logging.getLogger("hhcli.http")
BASE_URL = "https://api.hh.ru"
RETRY_STATUS = {429, 500, 502, 503, 504}


def _headers(auth: bool) -> dict[str, str]:
    h = {
        "User-Agent": get_user_agent(),
        "Accept": "application/json",
    }
    if auth:
        token = get_access_token()
        if token:
            h["Authorization"] = f"Bearer {token}"
    return h


def _respect_limits(resp: requests.Response) -> None:
    # если на исходе лимит — подождём Reset
    remaining = resp.headers.get("X-RateLimit-Remaining")
    reset = resp.headers.get("X-RateLimit-Reset")
    try:
        if remaining is not None and remaining.isdigit() and int(remaining) <= 1 and reset:
            time.sleep(float(reset))
    except Exception:
        pass


def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    auth: bool = False,
    retries: int = 3,
    timeout: int = 30,
) -> Any:
    url = f"{BASE_URL}{path}"
    backoff = 1.0
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.request(
                method,
                url,
                params=params,
                json=json,
                headers=_headers(auth),
                timeout=timeout,
            )
            if resp.status_code == 429 and attempt + 1 < retries:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        time.sleep(float(retry_after))
                    except Exception:
                        time.sleep(backoff)
                else:
                    time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code in RETRY_STATUS and attempt + 1 < retries:
                time.sleep(backoff)
                backoff *= 2
                continue

            # не ретраем — проверим/подождём лимит и вернём/поднимем
            if 200 <= resp.status_code < 300:
                _respect_limits(resp)
                return resp.json() if resp.text else None

            # Ошибка: выведем полезные детали и бросим
            try:
                body = resp.text
            except Exception:
                body = ""
            logger.error("HTTP %s %s -> %s: %s", method, url, resp.status_code, body)
            resp.raise_for_status()

        except Exception as e:
            last_exc = e
            if attempt + 1 < retries:
                time.sleep(backoff)
                backoff *= 2
            else:
                raise
    if last_exc:
        raise last_exc
