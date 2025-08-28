# hhcli — README, Troubleshooting & Dev Guide

> CLI и веб-интерфейс для работы с API hh.ru (поиск вакансий, справочники, OAuth вход, выгрузка CSV/JSONL/Parquet). Без БД.

---

## Содержание
- [Возможности](#возможности)
- [Структура проекта](#структура-проекта)
- [Быстрый старт](#быстрый-старт)
- [Конфигурация и OAuth](#конфигурация-и-oauth)
- [CLI — команды](#cli--команды)
- [WebApp (Streamlit)](#webapp-streamlit)
- [Переменные окружения](#переменные-окружения)
- [Экспорт данных](#экспорт-данных)
- [Логирование и отладка](#логирование-и-отладка)
- [Частые проблемы (Troubleshooting)](#частые-проблемы-troubleshooting)
- [Тестирование](#тестирование)
- [Стиль кода и инструменты](#стиль-кода-и-инструменты)
- [Roadmap](#roadmap)

---

## Возможности
- CLI-команды для справочников (`areas`, `roles`, `dicts`), поиска (`search`, `export`), профиля (`me`), резюме (`my-resumes`) и проверки отклика (`can-respond`).
- WebApp на Streamlit: фильтры (текст, локация, роли, график), таблица результатов, скачивание в CSV/JSONL/Parquet.
- OAuth-вход (read+resumes+negotiations), автообмен `code→token`, проверка `/me` и просмотр `/resumes/mine`.
- Ретраи и уважение лимитов API (`Retry-After`, `X-RateLimit-*`).
- Поддержка ENV-переменных поверх `~/.hhcli/config.json`.

---

## Структура проекта
```
hhcli/
  __init__.py
  main.py              # точка входа для CLI
  cli.py               # команды Typer
  config.py            # конфиг и ENV overlay
  http.py              # HTTP-клиент, ретраи, rate limit
  auth.py              # OAuth URL, exchange, refresh
  utils.py             # форматтеры, пагинация
  web_app.py           # Streamlit UI
  api/
    __init__.py
    vacancies.py
    employers.py
    areas.py
    resumes.py
    negotiations.py    # заготовка под отклики
    professional_roles.py
    dictionaries.py
```

---

## Быстрый старт
```bash
# зависимости CLI
pip install requests typer[all]

# WebApp
pip install streamlit pandas  # + pyarrow (для Parquet)

# Запуск CLI
python -m hhcli.main --help

# Запуск WebApp
streamlit run hhcli/web_app.py
```

---

## Конфигурация и OAuth
- Конфиг: `~/.hhcli/config.json` (создаётся автоматически).
- ENV-переменные перекрывают файл (см. ниже).
- Запрос скоупов: `read+resumes+negotiations`.
- Redirect URI по умолчанию для локалки: `http://localhost:8501`.

```bash
# записать client_id/secret/redirect_uri
python -m hhcli.main config --client-id "..." --client-secret "..." --redirect-uri "http://localhost:8501"

# получить ссылку на вход
python -m hhcli.main oauth-url

# обменять code на токены
python -m hhcli.main oauth-exchange CODE

# обновить access_token
python -m hhcli.main oauth-refresh

# проверить токен
python -m hhcli.main me
```

> В WebApp те же операции доступны в секции «Вход в hh.ru (OAuth)».

---

## CLI — команды
```text
config           Сохранить client_id/secret/redirect_uri/user_agent
oauth-url        Вывести ссылку на авторизацию
oauth-exchange   Обменять code на токены
oauth-refresh    Обновить access_token
areas [--parent] Дерево регионов/дети узла
roles            Проф. роли (id/названия)
dicts            Справочники (schedule и др.)
employer <id>    Инфо о работодателе
vacancy <id>     Инфо о вакансии
search           Поиск вакансий (фильтры: text, area, role, schedule, ...)
export           Экспорт вакансий (CSV/JSONL/Parquet)
my-resumes       Список резюме (нужен токен)
can-respond <id> Доступные резюме для отклика (нужен токен)
me               Профиль /me (нужен токен)
```

Примеры:
```bash
python -m hhcli.main areas --parent 113
python -m hhcli.main roles
python -m hhcli.main search --text "Python" --area 1 --role 96 --schedule remote --per-page 10
python -m hhcli.main export --text "Java" --area 2 --fmt jsonl --limit 300 --out spb_java.jsonl
```

---

## WebApp (Streamlit)
- Запуск: `streamlit run hhcli/web_app.py`
- Сайдбар: фильтры (text, area, roles, schedule, per_page, limit)
- Таблица результатов: кликабельные `url`, кнопка «Скачать» (CSV/JSONL/Parquet)
- Встроенная секция OAuth: ввод `client_id/secret/redirect_uri`, генерация ссылки, автообмен `code`, `/me`, `Мои резюме`.

---

## Переменные окружения
CLI и WebApp читают ENV поверх файла конфига:
```
HH_CLIENT_ID, HH_CLIENT_SECRET, HH_REDIRECT_URI, HH_USER_AGENT,
HH_ACCESS_TOKEN, HH_REFRESH_TOKEN
```

**PowerShell:**
```powershell
$env:HH_CLIENT_ID="..."; $env:HH_CLIENT_SECRET="..."; python -m hhcli.main me
```
**CMD:**
```bat
set HH_CLIENT_ID=... & set HH_CLIENT_SECRET=... & python -m hhcli.main me
```
**bash/zsh:**
```bash
export HH_CLIENT_ID=... HH_CLIENT_SECRET=...; python -m hhcli.main me
```

---

## Экспорт данных
- **CSV** — совместим с Excel; кодировка UTF‑8.
- **JSONL** — построчный JSON (удобно для аналитики/бигдаты).
- **Parquet** — колоночный формат (требует `pyarrow`).

CLI:
```bash
python -m hhcli.main export --text "Python" --area 1 --fmt parquet --out vacancies.parquet
```
WebApp: выпадающий список формата под таблицей.

---

## Логирование и отладка
В `http.py` включён логгер `hhcli.http` с выводом тела ответа при ошибке. Включить INFO/DEBUG:
```python
# в начале программы/скрипта
import logging
logging.basicConfig(level=logging.INFO)
# logging.getLogger("hhcli.http").setLevel(logging.DEBUG)
```

Для Streamlit:
```bash
streamlit run hhcli/web_app.py --logger.level=info
```

Ретраи/лимиты:
- 429 — учитывается `Retry-After`.
- `X-RateLimit-Remaining`/`Reset` — мягкая пауза при остатке <= 1.

---

## Частые проблемы (Troubleshooting)
**`ImportError: attempted relative import with no known parent package`**
- Запускайте CLI так: `python -m hhcli.main ...` из корня проекта.
- Для Streamlit используйте абсолютные импорты (`from hhcli.api import ...`).

**`SyntaxError: from __future__ ... must occur at the beginning`**
- Директива должна быть первой строкой файла.

**`IndentationError`**
- Проверьте отступы (4 пробела, без табов). В PyCharm — `Ctrl+Alt+L`.

**`ModuleNotFoundError: No module named 'requests'`**
- Установите зависимости в текущем venv: `pip install requests typer[all]`.

**`400 Bad Request /token`**
- Проверьте: `client_id/secret`, точное совпадение `redirect_uri`, `code` не протух/не использован.

**`401 Unauthorized`**
- Истёк token → выполните `oauth-refresh` или заново авторизуйтесь.

**`403 Forbidden` на /resumes/mine**
- Нужны скоупы `resumes` (+ `read`). Входите **аккаунтом соискателя**. Проверьте `/me`.

**Лимиты**
- Частые запросы → соблюдайте `Retry-After`, не ставьте `per_page=100` без необходимости, используйте `limit`.

---

## Тестирование
Минимум:
- Юнит-тесты для `utils.format_salary` и `utils.paginate_vacancies` (моки).
- Интеграционный тест `/vacancies` с VCR (pytest + `pytest-recording`).

Шаблон `tests/test_utils.py`:
```python
from hhcli.utils import format_salary

def test_format_salary_none():
    assert format_salary(None) == ""

def test_format_salary_full():
    s = {"from": 100000, "to": 200000, "currency": "RUR", "gross": True}
    assert "от 100000" in format_salary(s)
    assert "до 200000" in format_salary(s)
    assert "RUR" in format_salary(s)
```

Запуск:
```bash
pip install pytest
pytest -q
```

---

## Стиль кода и инструменты
Рекомендовано:
- **Black** (форматирование): `pip install black` → `black hhcli`
- **ruff** (линтер): `pip install ruff` → `ruff check hhcli`
- **pre-commit**: настроить хуки на `black`/`ruff`.

`.editorconfig` (минимум):
```
root = true
[*]
indent_style = space
indent_size = 4
charset = utf-8
end_of_line = lf
insert_final_newline = true
```

---

## Roadmap
- Отклики/переписка: `POST /negotiations` + UI-кнопка «Откликнуться».
- Сохранённые поиски/избранное (локальный JSON, без БД).
- Больше фильтров: опыт/зарплата/тип занятости.
- Dockerfile и публикация образа.
- Кэш справочников в `~/.hhcli/cache` с TTL.
- CI (GitHub Actions): lint + tests.

---

## Лицензия
MIT (или иная по вашему выбору).

![CI](https://github.com/StanBoron/hhcli/actions/workflows/ci.yml/badge.svg)

