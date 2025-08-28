from hhcli.utils import (
    format_salary,
    paginate_vacancies,
)


def test_format_salary_empty():
    assert format_salary(None) == ""

def test_format_salary_full():
    salary = {"from": 100000, "to": 200000, "currency": "RUR", "gross": True}
    out = format_salary(salary)
    assert "от 100000" in out
    assert "до 200000" in out
    assert "RUR" in out

def test_paginate_vacancies_basic():
    # эмулируем API: всего 3 страницы по 2 элемента = 6 вакансий
    def fake_fetch(page: int, per_page: int):
        total_pages = 3
        start = page * per_page
        items = [{"id": f"vac-{i}"} for i in range(start, start + per_page)]
        return {"items": items, "pages": total_pages}

    # без limit получим все 6
    got = list(paginate_vacancies(fake_fetch, per_page=2, limit=None))
    assert len(got) == 6
    assert got[0]["id"] == "vac-0"
    assert got[-1]["id"] == "vac-5"

def test_paginate_vacancies_limit():
    def fake_fetch(page: int, per_page: int):
        total_pages = 10
        start = page * per_page
        items = [{"id": f"vac-{i}"} for i in range(start, start + per_page)]
        return {"items": items, "pages": total_pages}

    # ограничим пятью элементами
    got = list(paginate_vacancies(fake_fetch, per_page=3, limit=5))
    assert len(got) == 5
    # первые пять, 0..4
    assert got[0]["id"] == "vac-0"
    assert got[-1]["id"] == "vac-4"
