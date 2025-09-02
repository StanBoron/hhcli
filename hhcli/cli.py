from __future__ import annotations

import csv
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, List, Dict, Optional

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
from hhcli.http import request
from hhcli.logs import setup_logging
from hhcli.respond import (
    CSV_HEADERS,
    get_vacancy_meta,
    read_ids_from_file,
    resume_allowed_for_vacancy,
    send_response,
    vacancy_has_required_test,
    vacancy_requires_letter,
)
from hhcli.respond.types import RespondResult as _RespondMassResult
from hhcli.utils import build_text_query, format_salary, paginate_vacancies

# -------------------- Логирование --------------------

setup_logging()
logger = logging.getLogger(__name__)

IGNORED_FILE = Path.home() / ".hhcli" / "ignored_negotiations.json"

NEG_UNIGNORE_IDS_ARG = typer.Argument(
    None,
    help="ID переговоров для удаления из ignore-листа.",
)


def _ignored_load() -> set[str]:
    try:
        data = json.loads(IGNORED_FILE.read_text(encoding="utf-8"))
        return set(str(x) for x in data if x)
    except Exception:
        return set()


def _ignored_save(ids: set[str]) -> None:
    IGNORED_FILE.parent.mkdir(parents=True, exist_ok=True)
    IGNORED_FILE.write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=2), encoding="utf-8")


def _ignore_negotiation_local(neg_id: str) -> tuple[bool, str]:
    """Локально «спрятать» переговор: добавить в ~/.hhcli/ignored_negotiations.json."""
    try:
        ids = _ignored_load()
        if neg_id in ids:
            return True, "ignored_local (already)"
        ids.add(str(neg_id))
        _ignored_save(ids)
        return True, "ignored_local"
    except Exception as e:
        return False, f"local_ignore_error: {e}"


# -------------------- Константы/фразы отказов --------------------

# Встречающиеся стейты отказов на стороне HH
_REFUSED_STATES = {
    "rejected",
    "refused",
    "declined",
    "finished",
    "employer_refused",
    "discard",  # фактический id в ленте переговоров
    "discarded",  # встречается в других эндпоинтах/старых ответах
}

# Дефолтные фразы, если JSON не найден/некорректен
_DEFAULT_REFUSAL_PHRASES: list[str] = [
    # короткие ключевые
    "отказ",
    "отклонен",
    "отклонена",
    "отклонено",
    # частые формулировки
    "не готовы пригласить вас на следующий этап",
    "мы выбрали другого кандидата",
    "к сожалению, мы вынуждены отказать",
    "решили отказать",
    "ваша кандидатура нам не подходит",
    "вашу кандидатуру отклонили",
    "отказались от продолжения",
    "не готовы продолжать",
]


def _load_refusal_phrases() -> list[str]:
    """
    Загружает список фраз отказа из hhcli/respond/refusal_phrases.json.
    Формат файла:
      либо массив строк: ["...", "..."]
      либо объект {"phrases": ["...", "..."]}

    Возвращает список в lowercase (casefold), без пустых и дублей.
    """
    candidates = [
        Path(__file__).parent / "respond" / "refusal_phrases.json",
        Path.cwd() / "hhcli" / "respond" / "refusal_phrases.json",
    ]
    for cfg_path in candidates:
        try:
            if not cfg_path.exists():
                continue
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            raw = data.get("phrases") if isinstance(data, dict) else data
            phrases: list[str] = []
            if isinstance(raw, list):
                for x in raw:
                    if isinstance(x, str):
                        s = x.strip()
                        if s:
                            phrases.append(s.casefold())
            phrases = sorted(set(phrases))
            if phrases:
                logger.info(
                    "Loaded refusal phrases from %s (%d items).",
                    cfg_path,
                    len(phrases),
                )
                return phrases
        except Exception as e:
            logger.warning(
                "Failed to load refusal phrases from %s (%s). Will try next candidate.",
                cfg_path,
                e,
            )
    logger.info(
        "Refusal phrases config not found/invalid. Using defaults (%d).",
        len(_DEFAULT_REFUSAL_PHRASES),
    )
    return [s.casefold() for s in _DEFAULT_REFUSAL_PHRASES if s.strip()]


_REFUSAL_PHRASES: list[str] = _load_refusal_phrases()

# -------------------- Typer app --------------------

app = typer.Typer(add_completion=False, help="CLI-инструмент для работы с API hh.ru")

# Typer defaults as module-level constants to avoid B008 in function signature
ARG_IDS_FILE = typer.Argument(
    ..., exists=True, readable=True, help="Файл со списком вакансий (.txt/.csv/.tsv/.jsonl)"
)
ARG_RESUME_ID = typer.Argument(..., help="ID резюме, которым откликаться")
OPT_MESSAGE = typer.Option(None, "--message", "-m", help="Сопроводительное письмо")
OPT_MESSAGE_FILE = typer.Option(
    None, "--message-file", "-mf", exists=True, readable=True, help="Файл с письмом (UTF-8)"
)
OPT_ONLY_CAN_RESPOND = typer.Option(
    False, "--only-can-respond/--no-only-can-respond", help="Проверять доступность резюме"
)
OPT_SKIP_TESTED = typer.Option(
    True, "--skip-tested/--no-skip-tested", help="Пропускать вакансии с обязательным тестом"
)
OPT_REQUIRE_LETTER = typer.Option(
    True,
    "--require-letter/--no-require-letter",
    help="Если у вакансии обязательно письмо — не отправлять без него",
)
OPT_RATE_LIMIT = typer.Option(0.7, "--rate-limit", min=0.0, help="Пауза между запросами, сек")
OPT_LIMIT = typer.Option(None, "--limit", min=1, help="Ограничить число откликов")
OPT_DRY_RUN = typer.Option(False, "--dry-run", help="Без отправки, только отчёт")
OPT_OUT = typer.Option(None, "--out", "-o", help="CSV-отчёт")


# === respond-mass: command (using shared utils) ===
@app.command("respond-mass")
def respond_mass(
    ids_file: Path = ARG_IDS_FILE,
    resume_id: str = ARG_RESUME_ID,
    message: str | None = OPT_MESSAGE,
    message_file: Path | None = OPT_MESSAGE_FILE,
    only_can_respond: bool = OPT_ONLY_CAN_RESPOND,
    skip_tested: bool = OPT_SKIP_TESTED,
    require_letter: bool = OPT_REQUIRE_LETTER,
    rate_limit: float = OPT_RATE_LIMIT,
    limit: int | None = OPT_LIMIT,
    dry_run: bool = OPT_DRY_RUN,
    out: Path | None = OPT_OUT,
) -> None:
    """Массовые отклики на вакансии."""
    # message from file (B904: chain exception)
    if message_file:
        try:
            with message_file.open("r", encoding="utf-8") as f:
                message = f.read().strip()
        except Exception as err:
            typer.secho(f"Не удалось прочитать message_file: {err}", fg=typer.colors.RED)
            raise typer.Exit(2) from err

    # Load IDs
    try:
        ids = read_ids_from_file(ids_file)
    except Exception as err:
        typer.secho(f"Ошибка чтения списка ID: {err}", fg=typer.colors.RED)
        raise typer.Exit(2) from err

    if not ids:
        typer.secho("Список вакансий пуст.", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    if limit is not None:
        ids = ids[:limit]

    typer.secho(f"К отклику подготовлено {len(ids)} вакансий.", fg=typer.colors.GREEN)

    results: list[_RespondMassResult] = []

    for idx, vacancy_id in enumerate(ids, start=1):
        prefix = f"[{idx}/{len(ids)}] #{vacancy_id} "
        meta = get_vacancy_meta(vacancy_id)

        if skip_tested and vacancy_has_required_test(meta):
            typer.secho(prefix + "пропущено: обязательный тест", fg=typer.colors.YELLOW)
            results.append(_RespondMassResult(vacancy_id=vacancy_id, status="skipped_test"))
            continue

        if require_letter and vacancy_requires_letter(meta) and not message:
            typer.secho(
                prefix + "пропущено: требуется сопроводительное письмо", fg=typer.colors.YELLOW
            )
            results.append(_RespondMassResult(vacancy_id=vacancy_id, status="skipped_no_letter"))
            continue

        if only_can_respond and not resume_allowed_for_vacancy(vacancy_id, resume_id):
            typer.secho(prefix + "пропущено: резюме не допускается", fg=typer.colors.YELLOW)
            results.append(_RespondMassResult(vacancy_id=vacancy_id, status="skipped_cannot"))
            continue

        if dry_run:
            typer.secho(prefix + "OK (dry-run)", fg=typer.colors.BLUE)
            results.append(_RespondMassResult(vacancy_id=vacancy_id, status="dry_run"))
        else:
            res = send_response(vacancy_id, resume_id, message)
            if res.status == "ok":
                typer.secho(prefix + "отправлено", fg=typer.colors.GREEN)
            else:
                typer.secho(prefix + f"ошибка: {res.error}", fg=typer.colors.RED)
            results.append(res)

        if rate_limit > 0 and idx < len(ids):
            time.sleep(rate_limit)

    # SIM115: context manager
    if out:
        with out.open("w", encoding="utf-8", newline="") as f_out:
            writer = csv.writer(f_out)
            writer.writerow(CSV_HEADERS)
            for r in results:
                writer.writerow(
                    [
                        r.vacancy_id,
                        r.status,
                        r.http_code or "",
                        r.negotiation_id or "",
                        r.error or "",
                        r.request_id or "",
                    ]
                )

    exit_code = (
        0
        if all(
            r.status in {"ok", "dry_run", "skipped_test", "skipped_no_letter", "skipped_cannot"}
            for r in results
        )
        else 1
    )
    raise typer.Exit(exit_code)


# === end of respond-mass ===

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
    elif "access_expires_at" in base and base["access_expires_at"] is not None:
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
    data = request("GET", "/me", auth=True)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


# -------------------- Справочники --------------------
def dicts_areas(*, parent: Optional[int] = None, flat: bool = True) -> List[Dict[str, Any]] | Dict[str, Any]:
    """
    Справочник регионов/городов hh.ru.
    - parent=None: страны верхнего уровня (или всё дерево, если flat=False)
    - parent=<area_id>: вернуть детей узла
    - flat=True: плоский список [{id, name, parent_id}] (читабельно для селектов)
      flat=False: вернуть исходную структуру HH (дерево)
    """
    if parent is None:
        tree = areas_api.get_areas_tree()
        if not flat:
            return tree
        out: List[Dict[str, Any]] = []
        def walk(nodes, parent_id=None):
            for n in nodes:
                out.append({"id": n["id"], "name": n["name"], "parent_id": parent_id})
                childs = n.get("areas") or []
                if childs:
                    walk(childs, n["id"])
        walk(tree)
        return out
    else:
        node = areas_api.get_area_node(parent)
        children = node.get("areas", [])
        if flat:
            return [{"id": ch["id"], "name": ch["name"], "parent_id": parent} for ch in children]
        return children

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
            for k in r:
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


# -------------------- Вспомогательные для переговоров --------------------


def _iter_negotiations(per_page: int = 100):
    logger.debug("iter_negotiations: start per_page=%d", per_page)
    page = 0
    while True:
        logger.debug("iter_negotiations: GET /negotiations page=%d", page)
        data = request(
            "GET", "/negotiations", params={"page": page, "per_page": per_page}, auth=True
        )
        items = (data or {}).get("items") or []
        logger.debug("iter_negotiations: page=%d items=%d", page, len(items))
        # --- новое: фильтр по ignore-листу ---
        ignored = _ignored_load()
        for _neg in items:
            _nid = str(_neg.get("id") or _neg.get("negotiation_id") or "")
            if _nid and _nid in ignored:
                logger.debug("iter_negotiations: skip locally ignored neg#%s", _nid)
                continue
            yield _neg
        # ---
        if not items or (data.get("page", page) >= data.get("pages", page)):
            logger.debug("iter_negotiations: stop")
            break
        page += 1


def _iter_active_negotiations(per_page: int = 100):
    """Итерирует активные отклики (status=active) постранично."""
    logger.debug("iter_active_negotiations: start per_page=%d", per_page)
    page = 0
    while True:
        logger.debug("iter_active_negotiations: GET /negotiations page=%d", page)
        data = request(
            "GET",
            "/negotiations",
            params={"page": page, "per_page": per_page, "status": "active"},
            auth=True,
        )
        items = (data or {}).get("items") or []
        logger.debug("iter_active_negotiations: page=%d items=%d", page, len(items))
        yield from items  # компактнее, чем for ...: yield ...
        if not items or (data.get("page", page) >= data.get("pages", page)):
            logger.debug("iter_active_negotiations: stop")
            break
        page += 1


def _normalize_text(s: str | None) -> str:
    return (s or "").strip().casefold()


def _text_has_refusal(text: str) -> bool:
    t = _normalize_text(text)
    if not t:
        return False
    return any(p in t for p in _REFUSAL_PHRASES)


def _parse_iso_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        if len(s2) > 5 and (s2[-5] in ["+", "-"]) and s2[-3] != ":":
            s2 = s2[:-2] + ":" + s2[-2:]
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def _fetch_last_message_text(neg: dict) -> str:
    """
    Тянем последнюю запись из /negotiations/{id}/messages.
    Используем messages_url, если он уже в элементе.
    """
    neg_id = str(neg.get("id") or neg.get("negotiation_id") or "")
    url = (neg.get("messages_url") or "").strip()
    if not url:
        if not neg_id:
            return ""
        url = f"/negotiations/{neg_id}/messages"

    try:
        data = request("GET", url, auth=True) or {}
        items = data.get("items") or []
        if not items:
            return ""
        last = items[-1]  # обычно по времени возрастают
        txt = last.get("text") or ""
        # Лог усечённого текста для отладки/обоснования
        short = _normalize_text(txt)[:300]
        logger.debug(
            "neg#%s last_message_api='%s' refused_by_api=%s", neg_id, short, _text_has_refusal(txt)
        )
        return txt or ""
    except Exception as e:
        logger.debug("neg#%s messages fetch error: %s", neg_id, e)
        return ""


def _is_refused(neg: dict) -> bool:
    """
    Логика:
      1) state.id ∈ _REFUSED_STATES (учитываем dict/str)
      2) иначе проверяем послед. сообщение по фразам (JSON + дефолты)
    """
    state = neg.get("state")
    sid = ""
    if isinstance(state, dict):
        sid = _normalize_text(state.get("id"))
    elif isinstance(state, str):
        sid = _normalize_text(state)

    if sid:
        is_ref = sid in _REFUSED_STATES
        logger.debug(
            "neg#%s state=%s -> refused=%s",
            neg.get("id") or neg.get("negotiation_id"),
            sid,
            is_ref,
        )
        if is_ref:
            return True

    # 2) Фразы в последнем сообщении
    # Сначала берём, если уже есть предзаполненное last_message
    last = (neg.get("last_message") or {}).get("text") or ""
    if not last:
        last = _fetch_last_message_text(neg)

    return _text_has_refusal(last)


def _close_or_archive_negotiation(neg_id: str) -> tuple[bool, str]:
    """[DEPRECATED ACTION] Для соискателя закрытие/архивация недоступны через API hh.ru.
    Вместо этого отмечаем переговор локально как «игнорируемый».
    """
    return _ignore_negotiation_local(neg_id)


def _leave_negotiation(neg_id: str) -> tuple[bool, str]:
    """Выйти из переговоров."""
    try:
        request("POST", f"/negotiations/{neg_id}/leave", auth=True)
        return True, "left"
    except Exception as err1:
        try:
            request("DELETE", f"/negotiations/{neg_id}/participants/me", auth=True)
            return True, "left"
        except Exception as err2:
            return False, f"{err1} | {err2}"


# -------------------- Команды по переговорам --------------------


# 1) Очистка откликов с отказами
@app.command("negotiations-clean-refused")
def negotiations_clean_refused(
    limit: int = typer.Option(
        0, "--limit", help="Ограничить количество закрытых/скрытых переписок (0 — без ограничения)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Только показать, без действий."),
    purge_local: bool = typer.Option(
        False, "--purge-local", help="Очистить локальный список скрытых переговоров и выйти."
    ),
) -> None:
    total = 0
    done = 0
    errors = 0

    if purge_local:
        try:
            if IGNORED_FILE.exists():
                IGNORED_FILE.unlink()
            typer.secho("Local ignore list cleared.", fg=typer.colors.GREEN)
        except Exception as e:
            typer.secho(f"Failed to clear ignore list: {e}", fg=typer.colors.RED)
        return

    for neg in _iter_negotiations():
        neg_id = str(neg.get("id") or neg.get("negotiation_id") or "")
        if not neg_id or not _is_refused(neg):
            continue
        total += 1
        prefix = f"[{total}] neg#{neg_id} "
        if dry_run:
            typer.secho(prefix + "would ignore locally (dry-run)", fg=typer.colors.BLUE)
        else:
            ok, msg = _close_or_archive_negotiation(neg_id)
            if ok:
                done += 1
                typer.secho(prefix + msg, fg=typer.colors.GREEN)
            else:
                errors += 1
                typer.secho(prefix + f"error: {msg}", fg=typer.colors.RED)
        if limit and done >= limit:
            break

    typer.secho(
        f"Processed: {total}; ignored_local: {done}; errors: {errors}",
        fg=typer.colors.GREEN if errors == 0 else typer.colors.YELLOW,
    )


@app.command("responses-delete")
def responses_delete(
    days: int = typer.Option(
        21, "--days", help="Порог давности для state=response (дни). По умолчанию 21."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Только показать, без удаления."),
    limit: int = typer.Option(
        0, "--limit", help="Ограничить число удалений (0 — без ограничения)."
    ),
) -> None:
    """
    Удаляет активные отклики:
      • state=discard — всегда,
      • state=response — если updated_at старше N дней.
    Переписки/чаты не трогаем.
    Требуются импорты: from datetime import datetime, timedelta, timezone
    И хелпер: _parse_iso_dt(s) -> datetime | None
    """
    now_utc = datetime.now(UTC)
    cutoff = now_utc - timedelta(days=days)

    checked = deleted = errors = 0
    reason_discard = 0
    reason_old = 0

    for item in _iter_active_negotiations():
        if item.get("hidden"):
            continue
        neg_id = str(item.get("id") or item.get("negotiation_id") or "")
        if not neg_id:
            continue

        state_id = ((item.get("state") or {}).get("id") or "").lower()
        is_discard = state_id == "discard"
        is_response = state_id == "response"

        is_old_response = False
        if is_response:
            dt = _parse_iso_dt(item.get("updated_at"))
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            is_old_response = bool(dt and dt < cutoff)

        if not (is_discard or is_old_response):
            continue

        checked += 1
        reason = "discard" if is_discard else f"response_older_{days}d"
        if dry_run:
            typer.secho(
                f"[{checked}] neg#{neg_id} ({reason}) would DELETE (dry-run)", fg=typer.colors.BLUE
            )
        else:
            try:
                resp = request(
                    "DELETE",
                    f"/negotiations/active/{neg_id}",
                    params={"with_decline_message": bool(item.get("decline_allowed") or False)},
                    auth=True,
                )
                # 204 No Content -> resp is None (успех), допускаем и {}
                if (resp is None) or (isinstance(resp, dict) and not resp):
                    deleted += 1
                    if is_discard:
                        reason_discard += 1
                    else:
                        reason_old += 1
                    typer.secho(
                        f"[{checked}] neg#{neg_id} ({reason}) deleted", fg=typer.colors.GREEN
                    )
                else:
                    typer.secho(
                        f"[{checked}] neg#{neg_id} ({reason}) unexpected response: {resp!r}",
                        fg=typer.colors.YELLOW,
                    )
            except Exception as e:
                errors += 1
                typer.secho(f"[{checked}] neg#{neg_id} ({reason}) error: {e}", fg=typer.colors.RED)

        if limit and deleted >= limit:
            break

    # Итоговая сводка
    summary = (
        f"Checked: {checked}; deleted: {deleted} "
        f"(discard: {reason_discard}, old>{days}d: {reason_old}); errors: {errors}"
    )
    typer.secho(summary, fg=typer.colors.GREEN if errors == 0 else typer.colors.YELLOW)


# 2) Выход из чатов с отказами
@app.command("negotiations-leave-refused")
def negotiations_leave_refused(
    limit: int = typer.Option(
        0, "--limit", help="Ограничить количество выходов (0 — без ограничения)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Только показать, без действий."),
) -> None:
    total = 0
    done = 0
    errors = 0
    for neg in _iter_negotiations():
        neg_id = str(neg.get("id") or neg.get("negotiation_id") or "")
        if not neg_id or not _is_refused(neg):
            continue
        total += 1
        prefix = f"[{total}] neg#{neg_id} "
        if dry_run:
            typer.secho(prefix + "would leave (dry-run)", fg=typer.colors.BLUE)
        else:
            ok, msg = _leave_negotiation(neg_id)
            if ok:
                done += 1
                typer.secho(prefix + msg, fg=typer.colors.GREEN)
            else:
                errors += 1
                typer.secho(prefix + f"error: {msg}", fg=typer.colors.RED)
        if limit and done >= limit:
            break
    typer.secho(
        f"Processed: {total}; left: {done}; errors: {errors}",
        fg=typer.colors.GREEN if errors == 0 else typer.colors.YELLOW,
    )


# 3) Автоподнятие резюме
@app.command("resume-autoraise")
def resume_autoraise(
    resume_id: str = typer.Argument(..., help="ID резюме для автоподнятия"),
    interval_hours: float = typer.Option(
        4.0, "--interval-hours", min=0.5, help="Интервал автоподнятия в часах."
    ),
    loop: bool = typer.Option(
        False, "--loop/--one-shot", help="Запускать в цикле (держит процесс)."
    ),
) -> None:
    def _raise_once() -> bool:
        try:
            request("POST", f"/resumes/{resume_id}/publish", auth=True)
            typer.secho("Resume published (raised).", fg=typer.colors.GREEN)
            return True
        except Exception as err:
            typer.secho(f"Failed to publish resume: {err}", fg=typer.colors.RED)
            return False

    ok = _raise_once()
    if not loop:
        raise typer.Exit(0 if ok else 1)

    seconds = max(1, int(interval_hours * 3600))
    typer.secho(f"Loop mode: will raise every {seconds} seconds.", fg=typer.colors.BLUE)
    while True:
        time.sleep(seconds)
        _raise_once()


@app.command("negotiations-show-ignored")
def negotiations_show_ignored(
    as_json: bool = typer.Option(False, "--json", help="Вывести список в формате JSON."),
    limit: int = typer.Option(
        0, "--limit", help="Ограничить количество выводимых ID (0 — без ограничений)."
    ),
    show_path: bool = typer.Option(
        False, "--show-path", help="Показать путь к файлу ignore-листа и выйти."
    ),
) -> None:
    """
    Показать текущий локальный список игнорируемых переговоров (~/.hhcli/ignored_negotiations.json).
    """
    if show_path:
        typer.secho(str(IGNORED_FILE), fg=typer.colors.BLUE)
        return

    ids = sorted(_ignored_load())
    if limit and len(ids) > limit:
        ids = ids[:limit]

    if as_json:
        typer.echo(json.dumps(ids, ensure_ascii=False, indent=2))
    else:
        if not ids:
            typer.secho("Ignored list is empty.", fg=typer.colors.YELLOW)
            return
        typer.secho(f"Ignored negotiations ({len(ids)}):", fg=typer.colors.GREEN)
        for i, nid in enumerate(ids, 1):
            typer.echo(f"{i:4d}. {nid}")


@app.command("negotiations-unignore")
def negotiations_unignore(
    ids: list[str] | None = NEG_UNIGNORE_IDS_ARG,
    all_: bool = typer.Option(
        False, "--all", help="Удалить все записи из ignore-листа (эквивалент purge)."
    ),
) -> None:
    """
    Удалить один/несколько переговоров из локального ignore-листа.
    """
    if all_:
        try:
            if IGNORED_FILE.exists():
                IGNORED_FILE.unlink()
            typer.secho("Local ignore list cleared (all).", fg=typer.colors.GREEN)
        except Exception as e:
            typer.secho(f"Failed to clear ignore list: {e}", fg=typer.colors.RED)
        return

    if not ids:
        typer.secho("Nothing to do: pass one or more IDs or use --all.", fg=typer.colors.YELLOW)
        return

    current = _ignored_load()
    removed = []
    for nid in ids:
        if nid in current:
            current.remove(nid)
            removed.append(nid)

    if removed:
        try:
            _ignored_save(current)
            typer.secho(f"Removed from ignore: {', '.join(removed)}", fg=typer.colors.GREEN)
        except Exception as e:
            typer.secho(f"Failed to save ignore list: {e}", fg=typer.colors.RED)
    else:
        typer.secho("No matching IDs found in ignore list.", fg=typer.colors.YELLOW)


# -------------------- Entry --------------------


def run():
    app()


if __name__ == "__main__":
    run()
