# hhcli/api/negotiations.py
from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from hhcli.http import request

LOG = logging.getLogger("hhcli.api.negotiations")


def create_response(
    vacancy_id: str,
    resume_id: str,
    *,
    message: str | None = None,
) -> dict[str, Any] | None:
    """
    POST /negotiations

    Важно:
    - hh иногда требует письмо (message). Если сервер отвечает "Letter required",
      вызовите повторно с непустым message.
    - Наш request() уже умеет fallback JSON→form на 400 bad_argument (по логам).
    """
    LOG.debug(
        "[NEGOTIATIONS] create_response inputs vacancy_id=%r resume_id=%r msg_len=%s",
        vacancy_id,
        resume_id,
        0 if not message else len(message),
    )

    payload: dict[str, Any] = {"vacancy_id": vacancy_id, "resume_id": resume_id}
    if message:
        payload["message"] = message.strip()

    LOG.debug("[NEGOTIATIONS] payload=%s", payload)
    resp = request("POST", "/negotiations", json=payload, auth=True)
    # /negotiations часто отвечает 201 без тела
    if not resp:
        LOG.debug("[NEGOTIATIONS] JSON ok -> <no body>")
        return None
    return resp


def delete_negotiation(negotiation_id: str) -> None:
    """
    DELETE /negotiations/{id}
    Может вернуть 403/405/409 — безопасно игнорируем.
    """
    with suppress(Exception):
        request("DELETE", f"/negotiations/{negotiation_id}", auth=True)


def list_negotiations(status: str | None = None, per_page: int = 50) -> list[dict[str, Any]]:
    """
    GET /negotiations (или /negotiations?status=messages | invited | discarded | ...)

    Возвращает список объектов переговоров (упрощённо items[] из API).
    """
    params: dict[str, Any] = {"per_page": per_page}
    if status:
        params["status"] = status
    data = request("GET", "/negotiations", params=params, auth=True) or {}
    items = data.get("items") or []
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict)]


def cleanup_rejections() -> tuple[int, list[str]]:
    """
    Удаляет/закрывает ветки переговоров со статусом 'discarded' (отказы).
    Возвращает (сколько_удалили, ошибки[]).
    """
    removed = 0
    errors: list[str] = []
    for it in list_negotiations(status="discarded", per_page=100):
        nid = str(it.get("id") or it.get("negotiation_id") or "")
        if not nid:
            continue
        try:
            delete_negotiation(nid)
            removed += 1
        except Exception as err:  # на всякий
            LOG.warning("Failed to delete negotiation %s: %s", nid, err)
            errors.append(f"{nid}: {err}")
    return removed, errors
