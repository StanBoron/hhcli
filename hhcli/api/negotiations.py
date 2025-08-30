from __future__ import annotations

import logging
from typing import Any

from hhcli.http import request

log = logging.getLogger("hhcli.api.negotiations")


def create_response(
    vacancy_id: str, resume_id: str, message: str | None = None
) -> dict[str, Any] | None:
    v = (vacancy_id or "").strip()
    r = (resume_id or "").strip()
    log.debug(
        "[NEGOTIATIONS] create_response inputs vacancy_id=%r resume_id=%r msg_len=%s",
        v,
        r,
        (len(message) if message else 0),
    )
    if not v or not r:
        log.error("[NEGOTIATIONS] empty ids v=%r r=%r", v, r)
        raise ValueError(f"vacancy_id/resume_id пусты: vacancy_id='{v}', resume_id='{r}'")

    payload: dict[str, Any] = {"vacancy_id": v, "resume_id": r}
    if message:
        payload["message"] = {"text": message}
    log.debug("[NEGOTIATIONS] payload=%s", payload)

    # 1) Пробуем JSON
    try:
        resp = request("POST", "/negotiations", json=payload, auth=True)
        log.debug("[NEGOTIATIONS] JSON ok -> %s", (resp if resp else "<no body>"))
        return resp
    except Exception as err:  # noqa: BLE001
        import requests  # локально

        bad_json = False
        if isinstance(err, requests.HTTPError) and err.response is not None:
            body = err.response.text or ""
            status = err.response.status_code
            log.error("[NEGOTIATIONS] JSON HTTP %s body=%s", status, body[:400].replace("\n", " "))
            # если сервер одновременно ругается на оба аргумента — часто это признак того,
            # что он не принял JSON как тело (редкий кейс у некоторых окружений)
            bad_json = status == 400 and '"vacancy_id"' in body and '"resume_id"' in body
        else:
            log.exception("[NEGOTIATIONS] JSON error: %s", err)

        if not bad_json:
            # если ошибка «иная», пробрасываем как есть
            raise

    # 2) На редкий случай — ретрай FORM (application/x-www-form-urlencoded)
    form_payload = {"vacancy_id": v, "resume_id": r}
    if message:
        # сервер в form не принимает nested msg; пошлём без него
        log.debug("[NEGOTIATIONS] FORM fallback (без message)")
    log.debug("[NEGOTIATIONS] FORM payload=%s", form_payload)
    resp2 = request("POST", "/negotiations", data=form_payload, auth=True)
    log.debug("[NEGOTIATIONS] FORM ok -> %s", (resp2 if resp2 else "<no body>"))
    return resp2


def list_negotiations(page: int = 0, per_page: int = 50) -> dict[str, Any]:
    return request("GET", "/negotiations", params={"page": page, "per_page": per_page}, auth=True)


def get_negotiation(negotiation_id: str) -> dict[str, Any]:
    return request("GET", f"/negotiations/{negotiation_id}", auth=True)


def vacancy_resumes(vacancy_id: str) -> dict[str, Any]:
    """GET /vacancies/{id}/resumes — какие резюме можно использовать для отклика на вакансию."""
    vid = (vacancy_id or "").strip()
    if not vid:
        raise ValueError("vacancy_id пуст")
    return request("GET", f"/vacancies/{vid}/resumes", auth=True)


def delete_negotiation(negotiation_id: str) -> None:
    """
    Для соискателя на HH реального DELETE нет — сервер отвечает 405 (method_not_allowed).
    Оставляем no-op-заглушку.
    """
    log.debug(
        "[NEGOTIATIONS] delete_negotiation called id=%s (no-op for applicant)", negotiation_id
    )
    return None
