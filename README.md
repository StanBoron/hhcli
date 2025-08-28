# hhcli

CLI и WebApp для работы с [hh.ru API](https://api.hh.ru/).  
Поддерживает поиск вакансий, работу с резюме, экспорт данных и авторизацию через OAuth.

## 🚀 Установка

Клонировать репозиторий:

```bash
git clone https://github.com/StanBoron/hhcli.git
cd hhcli
```

Установить зависимости:

```bash
pip install .
```

или (если используете poetry):

```bash
poetry install
```

## ⚙️ Авторизация

Для работы с приватными методами (резюме, отклики) требуется OAuth.

### Веб-интерфейс (WebApp)

Запуск:

```bash
streamlit run hhcli/web_app.py
```

В разделе **«Вход в hh.ru (OAuth)»** доступны:

- **Ссылка для авторизации** — переход на hh.ru и получение `code` для обмена.  
- **Ввести токены вручную** — если у вас уже есть `access_token` и `refresh_token`.  
- **Импорт токенов из JSON** — загрузите файл в формате:
  ```json
  {
    "token": {
      "access_token": "...",
      "refresh_token": "...",
      "access_expires_at": 1756723030
    }
  }
  ```
  или:
  ```json
  {
    "access_token": "...",
    "refresh_token": "...",
    "expires_in": 1209600
  }
  ```
- **Экспорт токенов в JSON** — можно скачать файл для переноса на другой компьютер.

### CLI (терминал)

#### Экспорт токенов

```bash
# nested-формат (совместим с WebApp)
python -m hhcli.main oauth-export --fmt nested --out tokens_nested.json

# flat-формат
python -m hhcli.main oauth-export --fmt flat --out tokens_flat.json
```

#### Импорт токенов

```bash
# nested
python -m hhcli.main oauth-import tokens_nested.json

# flat
python -m hhcli.main oauth-import tokens_flat.json
```

После импорта можно сразу проверить:

```bash
python -m hhcli.main me
```

⚠️ **Внимание:** токены являются секретными данными. Не публикуйте их и храните в безопасном месте!

## 🛠 Возможности

- Поиск вакансий (`search`)
- Информация о вакансии и работодателе (`vacancy`, `employer`)
- Справочники (`areas`, `dicts`, `roles`)
- Экспорт вакансий в CSV/JSONL/Parquet (`export`)
- Работа с резюме (`my-resumes`, `can-respond`)
- Отклики на вакансии (`respond`)
- Веб-интерфейс (Streamlit) с фильтрами и экспортом

## 👤 Автор

Проект создан пользователем [StanBoron](https://github.com/StanBoron).
