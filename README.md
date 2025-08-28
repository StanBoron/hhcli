# hhcli

[![CI](https://github.com/StanBoron/hhcli/actions/workflows/ci.yml/badge.svg)](https://github.com/StanBoron/hhcli/actions/workflows/ci.yml)

CLI и Web-приложение для работы с API [hh.ru](https://hh.ru).  
Позволяет искать вакансии, выгружать их в CSV/JSONL/Parquet, управлять резюме и выполнять авторизацию через OAuth.

---

## 🚀 Возможности

- 🔍 **Поиск вакансий** с фильтрацией по региону, роли, опыту, зарплате и т.д.
- 📄 **Информация о работодателях и вакансиях**
- 📂 **Экспорт вакансий** в CSV, JSONL или Parquet
- 👤 **Работа с резюме** (список своих резюме, проверка откликов)
- 🌍 **Справочники hh.ru** (регионы, роли, словари)
- 🖥️ **Веб-интерфейс** (на Streamlit) с поиском и выгрузкой вакансий
- 🔑 **Поддержка OAuth 2.0** для доступа к приватным данным пользователя

---

## 📦 Установка и запуск

Клонировать репозиторий:

```bash
git clone https://github.com/StanBoron/hhcli.git
cd hhcli
```

Установить зависимости (рекомендуется через виртуальное окружение):

```bash
pip install -r requirements.txt
```

---

## 🖥️ CLI-режим

Запуск через модуль:

```bash
python -m hhcli.main --help
```

Примеры команд:

```bash
# Сохранить client_id и client_secret
python -m hhcli.main config --client-id XXX --client-secret YYY --redirect-uri "https://example.com/callback"

# Ссылка для авторизации
python -m hhcli.main oauth-url

# Обмен кода на токен
python -m hhcli.main oauth-exchange CODE

# Поиск вакансий
python -m hhcli.main search --text "Python developer" --area 113 --per-page 20
```

---

## 🌐 Web-режим

Запустить Streamlit-приложение:

```bash
streamlit run hhcli/web_app.py
```

После этого интерфейс будет доступен в браузере (по умолчанию на [http://localhost:8501](http://localhost:8501)).

---

## ⚙️ Структура проекта

```
hhcli/
├── api/               # Работа с API hh.ru (вакансии, резюме, справочники и пр.)
├── tests/             # Тесты (pytest)
├── cli.py             # CLI-интерфейс (Typer)
├── web_app.py         # Streamlit Web-приложение
├── config.py          # Работа с конфигом и токенами
├── http.py            # HTTP-запросы с retry
├── utils.py           # Вспомогательные функции (форматирование, пагинация)
└── main.py            # Точка входа для CLI
```

---

## 🧪 Тестирование

Тесты написаны на `pytest`.

Запуск:

```bash
pytest -q
```

---

## 📌 TODO

- [ ] Добавить поддержку откликов на вакансии
- [ ] Реализовать "избранные" вакансии
- [ ] Улучшить UI веб-интерфейса
- [ ] Дополнить документацию API-примеров

---
