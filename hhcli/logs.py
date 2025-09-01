# hhcli/logs.py
from __future__ import annotations

import json
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any

DEFAULT_LOG_DIR = Path(os.path.expanduser("~")) / ".hhcli"
DEFAULT_LOG_FILE = DEFAULT_LOG_DIR / "hhcli.log"
DEFAULT_LEVEL = os.environ.get("HHCLI_LOG_LEVEL", "INFO").upper()


def _scrub_headers(h: dict[str, Any] | None) -> dict[str, Any]:
    if not h:
        return {}
    redacted = dict(h)
    for k in list(redacted.keys()):
        lk = str(k).lower()
        if lk in {"authorization", "proxy-authorization"}:
            redacted[k] = "***"
    return redacted


def _preview(obj: Any, limit: int = 1000) -> str:
    try:
        txt = (
            json.dumps(obj, ensure_ascii=False)
            if isinstance(obj, dict | list)  # ✅ заменили на X | Y
            else str(obj)
        )
    except Exception:
        txt = str(obj)
    return txt[:limit]


def setup_logging(
    level: str | None = None, file_path: str | os.PathLike[str] | None = None
) -> Path:
    """
    Конфигурирует логирование:
      - ротация по 1 МБ, хранит 5 файлов
      - маскирует Authorization
      - формат: ts level logger msg
    Уровень — HHCLI_LOG_LEVEL (по умолчанию INFO).
    Путь — HHCLI_LOG_FILE (по умолчанию ~/.hhcli/hhcli.log).
    """
    log_level = (level or DEFAULT_LEVEL).upper()
    log_file = Path(file_path or os.environ.get("HHCLI_LOG_FILE", DEFAULT_LOG_FILE))
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if getattr(root, "_hhcli_configured", False):
        return log_file

    root.setLevel(log_level)

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.setLevel(log_level)
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(log_level)
    root.addHandler(ch)

    root._hhcli_configured = True  # type: ignore[attr-defined]
    logging.getLogger(__name__).info("Logging initialized at %s, file=%s", log_level, log_file)
    return log_file


def http_log_request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None,
    json_body: dict[str, Any] | None,
    form_body: dict[str, Any] | None,
    headers: dict[str, Any] | None,
) -> None:
    log = logging.getLogger("hhcli.http")
    log.debug(
        "HTTP Request %s %s params=%s json=%s form=%s headers=%s",
        method,
        url,
        _preview(params),
        _preview(json_body),
        _preview(form_body),
        _preview(_scrub_headers(headers)),
    )


def http_log_response(
    method: str,
    url: str,
    *,
    status: int | str,
    elapsed_ms: int | None,
    text_preview: str,
    request_id: str | None,
) -> None:
    log = logging.getLogger("hhcli.http")
    rid = f", request_id={request_id}" if request_id else ""
    log.debug(
        "HTTP Response %s %s -> %s (%sms)%s body=%s",
        method,
        url,
        status,
        elapsed_ms if elapsed_ms is not None else "-",
        rid,
        text_preview,
    )


def parse_request_id(resp) -> str | None:
    try:
        rid = resp.headers.get("X-Request-Id") or resp.headers.get("Request-Id")
        if rid:
            return str(rid)
        data = resp.json()
        return data.get("request_id")
    except Exception:
        return None
