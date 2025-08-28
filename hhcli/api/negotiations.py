# hhcli/api/negotiations.py
from __future__ import annotations

import contextlib
from typing import Any

from hhcli.http import request


def create_response(vacancy_id: str, resume_id: str, message: str | None = None) -> dict[str, Any] | None:
    vacancy_id = (vacancy_id or "").strip()
    resume_id = (resume_id or "").strip()
    if not vacancy_id or not resume_id:
        raise ValueError("vacancy_id и/или resume_id не заданы")

    payload: dict[str, Any] = {"vacancy_id": vacancy_id, "resume_id": resume_id}
    if message:
        payload["message"] = {"text": message}
    return request("POST", "/negotiations", json=payload, auth=True)



def list_negotiations(page: int = 0, per_page: int = 50) -> dict[str, Any]:
    """GET /negotiations — список переписок/откликов."""
    return request("GET", "/negotiations", params={"page": page, "per_page": per_page}, auth=True)


def get_negotiation(negotiation_id: str) -> dict[str, Any]:
    """GET /negotiations/{id} — карточка отклика/приглашения."""
    return request("GET", f"/negotiations/{negotiation_id}", auth=True)


def delete_negotiation(negotiation_id: str) -> None:
    """
    Для соискателя на HH реального DELETE нет — сервер отвечает 405 (method_not_allowed).
    Оставляем no-op-заглушку, чтобы существующий код не падал.
    """
    with contextlib.suppress(Exception):
        # Исторически пытались DELETE -> 405. Если API когда-нибудь добавит архивирование —
        # здесь можно будет включить реальный запрос.
        pass
