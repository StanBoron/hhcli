from __future__ import annotations

from typing import Any

from hhcli.http import request


def search_vacancies(**params) -> dict[str, Any]:
    # Фильтры: text, area, salary, experience, only_with_salary, page, per_page, etc.
    # Не передаем ключи с None, чтобы не засорять запрос
    clean = {k: v for k, v in params.items() if v is not None}
    return request("GET", "/vacancies", params=clean)


def get_vacancy(vacancy_id: str) -> dict[str, Any]:
    return request("GET", f"/vacancies/{vacancy_id}")


def vacancy_resumes(vacancy_id: str) -> dict[str, Any]:
    return request("GET", f"/vacancies/{vacancy_id}/resumes", auth=True)
