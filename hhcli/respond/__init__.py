# hhcli/respond/__init__.py

from __future__ import annotations

# --- types --------------------------------------------------------------
try:
    from .types import RespondResult, RespondStatus
except Exception:
    # минимальные заглушки, если файл types изменился/отсутствует
    from dataclasses import dataclass

    class RespondStatus:
        OK = "ok"
        DRY_RUN = "dry_run"
        SKIPPED_TEST = "skipped_test"
        SKIPPED_NO_LETTER = "skipped_no_letter"
        SKIPPED_CANNOT = "skipped_cannot"
        ERROR = "error"

    @dataclass
    class RespondResult:
        vacancy_id: int
        status: str
        http_code: int | None = None
        negotiation_id: str | None = None
        error: str | None = None
        request_id: str | None = None


# --- constants ----------------------------------------------------------
try:
    from .constants import (
        CSV_HEADERS,
        OPT_DRY_RUN,
        OPT_IDS,
        OPT_IDS_FILE,
        OPT_LETTER,
        OPT_LIMIT,
        OPT_OUT,
        OPT_SKIP_NO_LETTER,
        OPT_SKIP_TESTED,
    )
except Exception:
    # если constants переименён — дадим минимальные заглушки,
    # чтобы импорт cli.py не падал
    CSV_HEADERS = ["vacancy_id", "status", "http_code", "negotiation_id", "error", "request_id"]
    OPT_LIMIT = None
    OPT_DRY_RUN = None
    OPT_OUT = None
    OPT_IDS = None
    OPT_IDS_FILE = None
    OPT_LETTER = None
    OPT_SKIP_TESTED = None
    OPT_SKIP_NO_LEТTER = None  # опечатка в рус. буквенном названии не критична


# --- mass utils: единая точка экспорта ---------------------------------
from .mass_utils import (
    extract_ids_from_text,  # извлечение id из произвольного текста
    get_vacancy_meta,  # мета вакансии
    read_ids_from_bytes,  # чтение id из bytes/строки
    read_ids_from_file,  # чтение id из файла
    resume_allowed_for_vacancy,  # резюме подходит по доступу?
    send_response,  # отправка отклика
    vacancy_has_required_test,  # есть обязательный тест?
    vacancy_requires_letter,  # требуется сопроводительное?
)

# Поддерживаем прежние алиасы, чтобы cli.py и внешний код не ломались:
rm_get_vacancy_meta = get_vacancy_meta
rm_has_required_test = vacancy_has_required_test
rm_requires_letter = vacancy_requires_letter
rm_resume_allowed = resume_allowed_for_vacancy
rm_send_response = send_response
rm_read_ids = read_ids_from_file
rm_read_ids_from_bytes = read_ids_from_bytes
rm_extract_ids = extract_ids_from_text

__all__ = [
    # types
    "RespondResult",
    "RespondStatus",
    # constants
    "CSV_HEADERS",
    "OPT_LIMIT",
    "OPT_DRY_RUN",
    "OPT_OUT",
    "OPT_IDS",
    "OPT_IDS_FILE",
    "OPT_LETTER",
    "OPT_SKIP_TESTED",
    "OPT_SKIP_NO_LETTER",
    # primary names
    "get_vacancy_meta",
    "vacancy_has_required_test",
    "vacancy_requires_letter",
    "resume_allowed_for_vacancy",
    "send_response",
    "read_ids_from_file",
    "read_ids_from_bytes",
    "extract_ids_from_text",
    # legacy aliases
    "rm_get_vacancy_meta",
    "rm_has_required_test",
    "rm_requires_letter",
    "rm_resume_allowed",
    "rm_send_response",
    "rm_read_ids",
    "rm_read_ids_from_bytes",
    "rm_extract_ids",
]
