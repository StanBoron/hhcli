from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RespondResult:
    vacancy_id: str
    status: str  # "ok" | "error" | "dry_run" | "skipped_*"
    http_code: int | None = None
    negotiation_id: str | None = None
    error: str | None = None
    request_id: str | None = None
