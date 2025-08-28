from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def format_salary(salary: dict[str, Any] | None) -> str:
    if not salary:
        return ""
    f = salary.get("from")
    t = salary.get("to")
    cur = salary.get("currency")
    gross = salary.get("gross")
    parts: list[str] = []
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
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
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


def build_text_query(
    *,
    name_kw: list[str] | None = None,
    name_not: list[str] | None = None,
    company_kw: list[str] | None = None,
    company_not: list[str] | None = None,
    desc_kw: list[str] | None = None,
    desc_not: list[str] | None = None,
    mode: str = "or",  # "or" | "and"
) -> str:
    """
    Собирает расширенный запрос для параметра `text` hh.ru с использованием префиксов:
    NAME:/COMPANY_NAME:/DESCRIPTION: и операторов AND/OR/NOT.

    - *_kw  — ключевые слова, которые ДОЛЖНЫ совпасть в поле
    - *_not — ключевые слова, которые НЕЛЬЗЯ допускать в поле

    Пример результата:
      (NAME:"Senior Python" AND NAME:Backend) AND (COMPANY_NAME:Stripe OR COMPANY_NAME:Google) NOT (DESCRIPTION:support)
    """

    def quote_if_needed(s: str) -> str:
        s = s.strip()
        if not s:
            return ""
        if " " in s or "\t" in s:
            return f'"{s}"'
        return s

    def build_block(field: str, kws: list[str] | None, logic: str) -> str | None:
        if not kws:
            return None
        toks = [f"{field}:{quote_if_needed(k)}" for k in kws if k and k.strip()]
        if not toks:
            return None
        joiner = " OR " if logic.lower() == "or" else " AND "
        return f"({joiner.join(toks)})"

    parts: list[str] = []

    # include-блоки
    inc_blocks = [
        build_block("NAME", name_kw, mode),
        build_block("COMPANY_NAME", company_kw, mode),
        build_block("DESCRIPTION", desc_kw, mode),
    ]
    inc_blocks = [b for b in inc_blocks if b]
    if inc_blocks:
        # склеиваем include-блоки между собой выбранной логикой
        joiner = " OR " if mode.lower() == "or" else " AND "
        parts.append(joiner.join(inc_blocks))

    # exclude-блоки — для каждого поля свой NOT-блок
    for field, kws in [
        ("NAME", name_not),
        ("COMPANY_NAME", company_not),
        ("DESCRIPTION", desc_not),
    ]:
        b = build_block(field, kws, "or")  # внутри NOT объединяем через OR
        if b:
            parts.append(f"NOT {b}")

    if not parts:
        return ""
    # итоговая склейка всех частей — также в соответствии с выбранным режимом
    final_joiner = " OR " if mode.lower() == "or" else " AND "
    return final_joiner.join(parts)
