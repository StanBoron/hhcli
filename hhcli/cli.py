from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Annotated, Any

import typer

from hhcli.api import (
    areas as areas_api,
)
from hhcli.api import (
    dictionaries,
    employers,
    negotiations,
    professional_roles,
    resumes,
    vacancies,
)
from hhcli.auth import build_oauth_url, exchange_code, refresh_access_token, set_tokens
from hhcli.config import load_config, save_config
from hhcli.utils import build_text_query, format_salary, paginate_vacancies

app = typer.Typer(add_completion=False, help="CLI-инструмент для работы с API hh.ru")


# -------------------- Конфиг и OAuth --------------------


@app.command("config")
def configure(
    client_id: Annotated[str | None, typer.Option(help="OAuth client_id")] = None,
    client_secret: Annotated[str | None, typer.Option(help="OAuth client_secret")] = None,
    redirect_uri: Annotated[str | None, typer.Option(help="Redirect URI")] = None,
    user_agent: Annotated[str | None, typer.Option(help="User-Agent для запросов к API")] = None,
):
    """Сохранить client_id, secret, redirect_uri, user_agent в конфиг."""
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
    typer.echo("Конфиг сохранён.")


@app.command("oauth-url")
def oauth_url():
    """Сгенерировать ссылку для авторизации (authorization_code)."""
    url = build_oauth_url()
    typer.echo(url)


@app.command("oauth-exchange")
def oauth_exchange(code: Annotated[str, typer.Argument(help="Код из редиректа (?code=...)")]):
    """Обмен кода из браузера на токены."""
    exchange_code(code)
    typer.echo("Токен сохранён.")


@app.command("oauth-export")
def oauth_export(
    fmt: Annotated[str, typer.Option(help="Формат JSON: nested|flat")] = "nested",
    out: Annotated[str, typer.Option(help="Файл назначения")] = "hh_tokens.json",
):
    """
    Экспорт текущих токенов в JSON-файл.

    Форматы:
      - nested: {"token": {"access_token": "...", "refresh_token": "...", "access_expires_at": 1234567890}}
      - flat:   {"access_token": "...", "refresh_token": "...", "expires_in": 12345}
    """
    fmt_l = fmt.strip().lower()
    if fmt_l not in {"nested", "flat"}:
        typer.secho("fmt должен быть 'nested' или 'flat'", fg=typer.colors.RED)
        raise typer.Exit(2)

    cfg = load_config()
    access_token = cfg.get("access_token") or ""
    refresh_token = cfg.get("refresh_token") or ""
    token_expires_at = int(cfg.get("token_expires_at") or 0)
    expires_in_now = max(0, token_expires_at - int(time.time())) if token_expires_at else 0

    if fmt_l == "nested":
        export_obj = {
            "token": {
                "access_token": access_token,
                "refresh_token": refresh_token or None,
                "access_expires_at": token_expires_at or None,
            }
        }
    else:  # flat
        export_obj = {
            "access_token": access_token,
            "refresh_token": refresh_token or None,
            "expires_in": expires_in_now,
        }

    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(export_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.secho(f"Tokens exported to {path}", fg=typer.colors.GREEN)


@app.command("oauth-import")
def oauth_import(
    src: Annotated[str, typer.Argument(help="Путь к JSON-файлу с токенами")],
):
    """
    Импорт токенов из JSON в конфиг (~/.hhcli/config.json).

    Поддерживаемые форматы:
      1) nested:
         {
           "token": {
             "access_token": "...",
             "refresh_token": "...",
             "access_expires_at": 1756723030
           }
         }
      2) flat:
         {
           "access_token": "...",
           "refresh_token": "...",
           "expires_in": 1209600
         }
    """
    path = Path(src)
    if not path.exists():
        typer.secho(f"Файл не найден: {path}", fg=typer.colors.RED)
        raise typer.Exit(2)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as err:
        typer.secho(f"Не удалось прочитать JSON: {err}", fg=typer.colors.RED)
        raise typer.Exit(2) from None

    # Унифицируем схему
    token_obj = data.get("token") if isinstance(data, dict) else None
    base = token_obj or data or {}

    access_token = base.get("access_token")
    refresh_token = base.get("refresh_token")

    # expires_in: берём напрямую, либо считаем из access_expires_at (unix)
    expires_in: int | None = None
    if "expires_in" in base and base["expires_in"] is not None:
        try:
            expires_in = int(base["expires_in"])
        except Exception:
            expires_in = None
    elif "access_expires_at" in base and base["access_expires_at"]:
        try:
            exp_at = int(base["access_expires_at"])
            expires_in = max(0, exp_at - int(time.time()))
        except Exception:
            expires_in = None

    if not access_token:
        typer.secho("В JSON отсутствует 'access_token'.", fg=typer.colors.RED)
        raise typer.Exit(2)

    # Сохраняем
    set_tokens(access_token, refresh_token or None, expires_in)
    typer.secho("Токены импортированы и сохранены.", fg=typer.colors.GREEN)


@app.command("oauth-refresh")
def oauth_refresh():
    """Обновить access_token по refresh_token."""
    refresh_access_token()
    typer.echo("access_token обновлён.")


@app.command("me")
def cmd_me():
    """Проверка токена: кто я (/me)."""
    from hhcli.http import request

    data = request("GET", "/me", auth=True)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


# -------------------- Справочники --------------------


@app.command("areas")
def cmd_areas(
    parent: Annotated[
        int | None, typer.Option(help="Показать детей для узла area_id (например, 113 = Россия)")
    ] = None,
):
    """Вывести страны/регионы верхнего уровня или детей узла --parent."""
    if parent is None:
        data = areas_api.get_areas_tree()
        for country in data:
            typer.echo(f"{country['id']}\t{country['name']}")
    else:
        node = areas_api.get_area_node(parent)
        for child in node.get("areas", []):
            typer.echo(f"{child['id']}\t{child['name']}")


@app.command("roles")
def cmd_roles():
    """Профессиональные роли (id, name)."""
    data = professional_roles.get_roles()
    for group in data.get("categories", []):
        gname = group.get("name", "")
        for r in group.get("roles", []):
            typer.echo(f"{r['id']}\t{r['name']}\t[{gname}]")


@app.command("dicts")
def cmd_dicts():
    """Часть словарей из /dictionaries (например, schedule)."""
    data = dictionaries.get_dictionaries()
    sched = data.get("schedule", []) or []
    typer.echo("schedule:")
    for s in sched:
        typer.echo(f"  {s['id']}\t{s['name']}")


# -------------------- Карточки работодателя/вакансии --------------------


@app.command("employer")
def cmd_employer(employer_id: Annotated[str, typer.Argument(help="ID работодателя")]):
    """Информация о работодателе /employers/{id}."""
    data = employers.get_employer(employer_id)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.command("vacancy")
def cmd_vacancy(vacancy_id: Annotated[str, typer.Argument(help="ID вакансии")]):
    """Информация о вакансии /vacancies/{id}."""
    data = vacancies.get_vacancy(vacancy_id)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


# -------------------- Поиск --------------------


@app.command("search")
def cmd_search(
    text: Annotated[
        str, typer.Option(help="Свободный текст (если не используете точечные поля)")
    ] = "",
    area: Annotated[int | None, typer.Option(help="area id (например, 1 = Москва)")] = None,
    experience: Annotated[
        str | None, typer.Option(help="noExperience|between1And3|between3And6|moreThan6")
    ] = None,
    salary: Annotated[int | None, typer.Option(help="Фильтр по зарплате 'от'")] = None,
    currency: Annotated[str | None, typer.Option(help="Валюта: RUR|USD|EUR")] = None,
    only_with_salary: Annotated[bool, typer.Option(help="Только с указанием зарплаты")] = False,
    page: Annotated[int, typer.Option(help="Номер страницы (0..)")] = 0,
    per_page: Annotated[int, typer.Option(help="Размер страницы (до 100)")] = 20,
    role: Annotated[list[int] | None, typer.Option(help="id роли (можно повторять опцию)")] = None,
    employment: Annotated[
        list[str] | None,
        typer.Option(help="full|part|project|volunteer|probation (можно повторять опцию)"),
    ] = None,
    name_kw: Annotated[
        list[str] | None, typer.Option(help="Слова в NAME (можно повторять)")
    ] = None,
    name_not: Annotated[
        list[str] | None, typer.Option(help="Исключить в NAME (можно повторять)")
    ] = None,
    company_kw: Annotated[
        list[str] | None, typer.Option(help="Слова в COMPANY_NAME (можно повторять)")
    ] = None,
    company_not: Annotated[
        list[str] | None, typer.Option(help="Исключить в COMPANY_NAME (можно повторять)")
    ] = None,
    desc_kw: Annotated[
        list[str] | None, typer.Option(help="Слова в DESCRIPTION (можно повторять)")
    ] = None,
    desc_not: Annotated[
        list[str] | None, typer.Option(help="Исключить в DESCRIPTION (можно повторять)")
    ] = None,
    kw_mode: Annotated[str, typer.Option(help="Логика include-блоков: or|and")] = "or",
    schedule: Annotated[str | None, typer.Option(help="id из словаря schedule")] = None,
    search_field: Annotated[str | None, typer.Option(help="name|company_name|description")] = None,
    order_by: Annotated[str | None, typer.Option(help="publication_time|relevance")] = None,
    date_from: Annotated[str | None, typer.Option(help="Дата с (YYYY-MM-DD или ISO)")] = None,
    date_to: Annotated[str | None, typer.Option(help="Дата по (YYYY-MM-DD или ISO)")] = None,
    with_address: Annotated[bool, typer.Option(help="Только с адресом")] = False,
    save_json: Annotated[
        str | None, typer.Option(help="Сохранить сырые items поиска в JSON")
    ] = None,
):
    text_built = build_text_query(
        name_kw=name_kw,
        name_not=name_not,
        company_kw=company_kw,
        company_not=company_not,
        desc_kw=desc_kw,
        desc_not=desc_not,
        mode=kw_mode,
    )
    effective_text = text_built or text
    """Поиск вакансий (/vacancies) с расширенными полями фильтрации."""
    params: dict[str, Any] = {
        "text": effective_text,
        "area": area,
        "experience": experience,
        "salary": salary,
        "currency": currency,
        "only_with_salary": "true" if only_with_salary else None,
        "page": page,
        "per_page": per_page,
        "professional_role": role,
        "employment": employment,
        "schedule": schedule,
        "search_field": search_field,
        "order_by": order_by,
        "date_from": date_from,
        "date_to": date_to,
        "with_address": "true" if with_address else None,
    }
    data = vacancies.search_vacancies(**params)
    items = data.get("items", []) if isinstance(data, dict) else []

    if save_json:
        Path(save_json).write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    for v in items:
        sal = format_salary(v.get("salary"))
        emp = (v.get("employer") or {}).get("name", "")
        typer.echo(f"{v.get('id','')}\t{v.get('name','')}\t{emp}\t{sal}")


# -------------------- Экспорт --------------------


@app.command("export")
def cmd_export(
    text: Annotated[str, typer.Option(help="Строка поиска")] = "",
    area: Annotated[int | None, typer.Option(help="area id")] = None,
    experience: Annotated[
        str | None, typer.Option(help="noExperience|between1And3|between3And6|moreThan6")
    ] = None,
    salary: Annotated[int | None, typer.Option(help="Фильтр по зарплате 'от'")] = None,
    currency: Annotated[str | None, typer.Option(help="Валюта: RUR|USD|EUR")] = None,
    only_with_salary: Annotated[bool, typer.Option(help="Только с указанием зарплаты")] = False,
    per_page: Annotated[int, typer.Option(help="до 100")] = 100,
    limit: Annotated[int | None, typer.Option(help="макс. вакансий для экспорта")] = None,
    out: Annotated[str, typer.Option(help="файл назначения")] = "vacancies.csv",
    role: Annotated[list[int] | None, typer.Option(help="id роли (можно повторять опцию)")] = None,
    employment: Annotated[
        list[str] | None,
        typer.Option(help="full|part|project|volunteer|probation (можно повторять опцию)"),
    ] = None,
    name_kw: Annotated[
        list[str] | None, typer.Option(help="Слова в NAME (можно повторять)")
    ] = None,
    name_not: Annotated[
        list[str] | None, typer.Option(help="Исключить в NAME (можно повторять)")
    ] = None,
    company_kw: Annotated[
        list[str] | None, typer.Option(help="Слова в COMPANY_NAME (можно повторять)")
    ] = None,
    company_not: Annotated[
        list[str] | None, typer.Option(help="Исключить в COMPANY_NAME (можно повторять)")
    ] = None,
    desc_kw: Annotated[
        list[str] | None, typer.Option(help="Слова в DESCRIPTION (можно повторять)")
    ] = None,
    desc_not: Annotated[
        list[str] | None, typer.Option(help="Исключить в DESCRIPTION (можно повторять)")
    ] = None,
    kw_mode: Annotated[str, typer.Option(help="Логика include-блоков: or|and")] = "or",
    schedule: Annotated[str | None, typer.Option(help="id из словаря schedule")] = None,
    search_field: Annotated[str | None, typer.Option(help="name|company_name|description")] = None,
    order_by: Annotated[str | None, typer.Option(help="publication_time|relevance")] = None,
    date_from: Annotated[str | None, typer.Option(help="Дата с (YYYY-MM-DD или ISO)")] = None,
    date_to: Annotated[str | None, typer.Option(help="Дата по (YYYY-MM-DD или ISO)")] = None,
    with_address: Annotated[bool, typer.Option(help="Только с адресом")] = False,
    fmt: Annotated[str, typer.Option(help="Формат: csv|jsonl|parquet")] = "csv",
    details: Annotated[bool, typer.Option(help="Тянуть детали вакансии (медленнее)")] = False,
):
    text_built = build_text_query(
        name_kw=name_kw,
        name_not=name_not,
        company_kw=company_kw,
        company_not=company_not,
        desc_kw=desc_kw,
        desc_not=desc_not,
        mode=kw_mode,
    )
    effective_text = text_built or text

    def fetch(page: int, per_page_: int):
        return vacancies.search_vacancies(
            text=effective_text,
            area=area,
            experience=experience,
            salary=salary,
            currency=currency,
            only_with_salary="true" if only_with_salary else None,
            page=page,
            per_page=per_page_,
            professional_role=role,
            employment=employment,
            schedule=schedule,
            search_field=search_field,
            order_by=order_by,
            date_from=date_from,
            date_to=date_to,
            with_address="true" if with_address else None,
        )

    rows: list[dict[str, Any]] = []
    for idx, v in enumerate(paginate_vacancies(fetch, per_page=per_page, limit=limit), start=1):
        base = {
            "id": v.get("id", ""),
            "name": v.get("name", ""),
            "employer": (v.get("employer") or {}).get("name", ""),
            "employer_id": (v.get("employer") or {}).get("id", ""),
            "salary": format_salary(v.get("salary")),
            "area": (v.get("area") or {}).get("name", ""),
            "published_at": v.get("published_at", ""),
            "alternate_url": v.get("alternate_url", ""),
        }

        # Попробуем вытащить немного из search-элемента, если есть
        base["schedule_id"] = (
            (v.get("schedule") or {}).get("id", "")
            if isinstance(v.get("schedule"), dict)
            else v.get("schedule", "")
        )
        base["employment_id"] = (
            (v.get("employment") or {}).get("id", "")
            if isinstance(v.get("employment"), dict)
            else v.get("employment", "")
        )
        addr = v.get("address") or {}
        base["address_city"] = addr.get("city", "")
        base["address_street"] = addr.get("street", "")
        base["address_raw"] = addr.get("raw", "")

        if details and base["id"]:
            try:
                det = vacancies.get_vacancy(str(base["id"])) or {}
                # Расширяем подробностями
                base["schedule_id"] = (det.get("schedule") or {}).get("id", base["schedule_id"])
                base["schedule_name"] = (det.get("schedule") or {}).get("name", "")
                base["employment_id"] = (det.get("employment") or {}).get(
                    "id", base["employment_id"]
                )
                base["employment_name"] = (det.get("employment") or {}).get("name", "")
                exp = det.get("experience") or {}
                base["experience_id"] = exp.get("id", "")
                base["experience_name"] = exp.get("name", "")
                addr = det.get("address") or {}
                base["address_city"] = addr.get("city", base["address_city"])
                base["address_street"] = addr.get("street", base["address_street"])
                base["address_raw"] = addr.get("raw", base["address_raw"])
                # key_skills: массив объектов с полем name
                ks = det.get("key_skills") or []
                base["key_skills"] = ", ".join(
                    sorted({(k or {}).get("name", "") for k in ks if (k or {}).get("name")})
                )
            # Можно добавить ещё: contacts, languages и т.п., если нужно
            except Exception:
                # Пропускаем enrichment для этой записи, чтобы экспорт не падал
                pass

        rows.append(base)
        if idx % 100 == 0:
            typer.echo(f"... собрали {idx}")

    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)

    fmt_l = fmt.lower()
    if fmt_l == "jsonl":
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
        )
    elif fmt_l == "parquet":
        try:
            import pandas as pd  # type: ignore
        except Exception:
            typer.secho(
                "Для Parquet нужен пакет pandas/pyarrow: pip install pandas pyarrow",
                fg=typer.colors.RED,
            )
            raise typer.Exit(2) from None
        df = pd.DataFrame(rows)
        df.to_parquet(path, index=False)
    else:
        # CSV: сформируем динамический заголовок по всем встреченным ключам
        all_keys: list[str] = []
        for r in rows:
            for k in r:  # было r.keys()
                if k not in all_keys:
                    all_keys.append(k)
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(all_keys)
            for r in rows:
                w.writerow([r.get(k, "") for k in all_keys])

    typer.secho(f"Exported {len(rows)} rows to {path}", fg=typer.colors.GREEN)


# -------------------- Резюме / Отклики --------------------


@app.command("my-resumes")
def cmd_my_resumes():
    """Список резюме текущего пользователя."""
    data = resumes.my_resumes()
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.command("can-respond")
def cmd_can_respond(vacancy_id: Annotated[str, typer.Argument(help="ID вакансии")]):
    """Какими резюме можно откликнуться на вакансию."""
    data = vacancies.vacancy_resumes(vacancy_id)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.command("respond")
def cmd_respond(
    vacancy_id: Annotated[str, typer.Argument(help="ID вакансии")],
    resume_id: Annotated[str, typer.Argument(help="ID резюме (см. my-resumes / can-respond)")],
    message: Annotated[str | None, typer.Option(help="Сообщение работодателю")] = None,
    validate: Annotated[
        bool, typer.Option(help="Проверить, что резюме подходит вакансии перед откликом")
    ] = True,
):
    """Откликнуться на вакансию (POST /negotiations)."""
    if validate:
        data = vacancies.vacancy_resumes(vacancy_id)
        ids = {r.get("id") for r in data.get("items", [])}
        if resume_id not in ids:
            typer.secho(
                "⚠️  Резюме не подходит этой вакансии (или нет права отклика).",
                fg=typer.colors.YELLOW,
            )
            if ids:
                typer.secho(
                    "   Подходящие резюме: " + ", ".join(sorted(filter(None, ids))),
                    fg=typer.colors.YELLOW,
                )
            raise typer.Exit(2)

    try:
        resp = negotiations.create_response(vacancy_id, resume_id, message)
    except Exception as err:
        # Если HTTPError — покажем тело ответа
        try:
            import requests  # type: ignore

            if isinstance(err, requests.HTTPError) and err.response is not None:
                typer.secho(
                    f"HTTP {err.response.status_code}: {err.response.text}",
                    fg=typer.colors.RED,
                )
        except Exception:
            pass
        raise
    typer.echo(json.dumps(resp, ensure_ascii=False, indent=2))


# -------------------- Entry --------------------


def run():
    app()


if __name__ == "__main__":
    run()
