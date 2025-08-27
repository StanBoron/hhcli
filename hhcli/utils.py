from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional


def format_salary(salary: Optional[Dict[str, Any]]) -> str:
    if not salary:
        return ""
    f = salary.get("from")
    t = salary.get("to")
    cur = salary.get("currency")
    gross = salary.get("gross")
    parts: List[str] = []
    if f:
        parts.append(f"от {f}")
    if t:
        parts.append(f"до {t}")
    if cur:
        parts.append(cur)
    if gross is not None:
        parts.append("gross" if gross else "net")
    return " ".join(parts)


def paginate_vacancies(
    fetch_page_fn,
    *,
    per_page: int = 100,
    limit: Optional[int] = None,
) -> Iterator[Dict[str, Any]]:
    """
    Итератор по всем вакансиям.

    `fetch_page_fn(page, per_page) -> dict` должен возвращать JSON от /vacancies.
    `limit` — максимум вакансий (None = все доступные в рамках API).
    """
    page = 0
    seen = 0
    while True:
        data = fetch_page_fn(page, per_page)
        items = data.get("items", [])
        if not items:
            break

        for it in items:
            yield it
            seen += 1
            if limit is not None and seen >= limit:
                return

        # остановка по числу страниц из ответа
        pages = data.get("pages") or 0
        if page >= pages - 1:
            break
        page += 1
