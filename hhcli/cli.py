from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional, List

import typer

from .config import load_config, save_config
from .auth import build_oauth_url, exchange_code, refresh_token
from .utils import format_salary, paginate_vacancies
from .api import vacancies, employers, areas, resumes
from .api import professional_roles, dictionaries

# создаём Typer-приложение
app = typer.Typer(add_completion=False)

# -------------------- Config & OAuth --------------------

@app.command("config")
def configure(
    client_id: Optional[str] = typer.Option(None),
    client_secret: Optional[str] = typer.Option(None),
    redirect_uri: Optional[str] = typer.Option(None),
    user_agent: Optional[str] = typer.Option(None),
):
    """Сохранить client_id, secret и т.п. в конфиг"""
    cfg = load_config()
    if client_id is not None:
        cfg["client_id"] = client_id
    if client_secret is not None:
        cfg["client_secret"] = client_secret
    if redirect_uri is not None:
        cfg["redirect_uri"] = redirect_uri
    if user_agent is not None:
        cfg["user_agent"] = user_agent
    save_config(cfg)
    typer.echo("Saved config.")


@app.command("oauth-url")
def oauth_url():
    """Ссылка для авторизации (authorization_code)"""
    typer.echo(build_oauth_url())


@app.command("oauth-exchange")
def oauth_exchange(code: str):
    """Обмен кода из браузера на токены"""
    exchange_code(code)
    typer.echo("Token saved.")


@app.command("oauth-refresh")
def oauth_refresh():
    """Обновить access_token по refresh_token"""
    refresh_token()
    typer.echo("Access token refreshed.")

# -------------------- Reference/Lookup --------------------

@app.command("areas")
def cmd_areas(parent: Optional[int] = typer.Option(None, help="Показать детей для узла area_id (например, 113 = Россия)")):
    """Вывести страны/регионы верхнего уровня или детей узла --parent"""
    if parent is None:
        data = areas.get_areas_tree()
        for country in data:
            typer.echo(f"{country['id']}\t{country['name']}")
    else:
        node = areas.get_area_node(parent)
        typer.echo(f"{node['id']}\t{node['name']}")
        for child in node.get("areas", []):
            typer.echo(f"{child['id']}\t{child['name']}")

@app.command("roles")
def cmd_roles():
    """Список professional_roles (id и названия)."""
    data = professional_roles.get_roles()
    for group in data.get("categories", []):
        typer.echo(f"[{group['id']}] {group['name']}")
        for r in group.get("roles", []):
            typer.echo(f"  {r['id']}\t{r['name']}")

@app.command("dicts")
def cmd_dicts():
    """Вывод части словарей (например, schedule)."""
    data = dictionaries.get_dictionaries()
    sched = data.get("schedule", [])
    typer.echo("schedule:")
    for s in sched:
        typer.echo(f"  {s['id']}\t{s['name']}")

@app.command("employer")
def cmd_employer(employer_id: str):
    """Инфо о работодателе"""
    data = employers.get_employer(employer_id)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))

@app.command("vacancy")
def cmd_vacancy(vacancy_id: str):
    """Инфо о вакансии"""
    data = vacancies.get_vacancy(vacancy_id)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))

# -------------------- Search --------------------

@app.command("search")
def cmd_search(
    text: str = typer.Option(""),
    area: Optional[int] = typer.Option(None),
    experience: Optional[str] = typer.Option(None, help="noExperience, between1And3, between3And6, moreThan6"),
    salary: Optional[int] = typer.Option(None),
    only_with_salary: bool = typer.Option(False),
    page: int = typer.Option(0),
    per_page: int = typer.Option(20),
    role: Optional[List[int]] = typer.Option(None, help="id роли (можно повторять опцию несколько раз)"),
    schedule: Optional[str] = typer.Option(None, help="id из словаря schedule (см. hhcli dicts)"),
    save_json: Optional[str] = typer.Option(None),
):
    """Поиск вакансий"""
    params = {
        "text": text,
        "area": area,
        "experience": experience,
        "salary": salary,
        "only_with_salary": "true" if only_with_salary else None,
        "page": page,
        "per_page": per_page,
        "professional_role": role,  # hh допускает множественные значения
        "schedule": schedule,
    }
    data = vacancies.search_vacancies(**params)
    if save_json:
        Path(save_json).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"Saved: {save_json}")
        return

    for v in data.get("items", []):
        sal = format_salary(v.get("salary"))
        emp = (v.get("employer") or {}).get("name", "")
        typer.echo(f"{v['id']}\t{v['name']}\t{emp}\t{sal}")

# -------------------- Export --------------------

@app.command("export")
def cmd_export(
    text: str = typer.Option(""),
    area: Optional[int] = typer.Option(None),
    experience: Optional[str] = typer.Option(None),
    salary: Optional[int] = typer.Option(None),
    only_with_salary: bool = typer.Option(False),
    per_page: int = typer.Option(100, help="до 100"),
    limit: Optional[int] = typer.Option(None, help="макс. вакансий для экспорта"),
    out: str = typer.Option("vacancies.csv"),
    role: Optional[List[int]] = typer.Option(None, help="id роли (можно повторять опцию)"),
    schedule: Optional[str] = typer.Option(None, help="id из словаря schedule"),
    fmt: str = typer.Option("csv", help="Формат: csv|jsonl|parquet"),
):
    """Выгрузить вакансии в CSV/JSONL/Parquet"""
    def fetch(page: int, per_page_: int):
        return vacancies.search_vacancies(
            text=text,
            area=area,
            experience=experience,
            salary=salary,
            only_with_salary="true" if only_with_salary else None,
            page=page,
            per_page=per_page_,
            professional_role=role,
            schedule=schedule,
        )

    rows: List[dict] = []
    count = 0
    for v in paginate_vacancies(fetch, per_page=per_page, limit=limit):
        rows.append({
            "id": v.get("id", ""),
            "name": v.get("name", ""),
            "employer": (v.get("employer") or {}).get("name", ""),
            "salary": format_salary(v.get("salary")),
            "area": (v.get("area") or {}).get("name", ""),
            "published_at": v.get("published_at", ""),
            "alternate_url": v.get("alternate_url", ""),
        })
        count += 1
        if count % 100 == 0:
            typer.echo(f"... собрали {count}")

    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)

    fmt_l = fmt.lower()
    if fmt_l == "jsonl":
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    elif fmt_l == "parquet":
        try:
            import pandas as pd  # type: ignore
        except Exception:
            typer.secho("Для Parquet нужен пакет pandas/pyarrow: pip install pandas pyarrow", fg=typer.colors.RED)
            raise typer.Exit(2)
        df = pd.DataFrame(rows)
        df.to_parquet(path, index=False)
    else:
        # CSV по умолчанию
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            header = ["id", "name", "employer", "salary", "area", "published_at", "alternate_url"]
            w.writerow(header)
            for r in rows:
                w.writerow([r[k] for k in header])

    typer.secho(f"Exported {len(rows)} rows to {path}", fg=typer.colors.GREEN)


# -------------------- Applicant --------------------

@app.command("my-resumes")
def cmd_my_resumes():
    """Список резюме текущего пользователя"""
    data = resumes.my_resumes()
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))

@app.command("can-respond")
def cmd_can_respond(vacancy_id: str):
    """Какими резюме можно откликнуться на вакансию"""
    data = vacancies.vacancy_resumes(vacancy_id)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))

@app.command("me")
def cmd_me():
    """Кто я (/me) — проверка токена"""
    from .http import request
    data = request("GET", "/me", auth=True)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))

