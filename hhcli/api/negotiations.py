from __future__ import annotations
from typing import Any, Dict


# Заглушка под будущие переговоры/отклики

def create_response(vacancy_id: str, resume_id: str, message: str | None = None) -> Dict[str, Any]:
    payload = {"vacancy_id": vacancy_id, "resume_id": resume_id}
    if message:
        pass
        payload["message"] = message
# Включите реальный endpoint, когда получите соответствующие права приложения
# return request("POST", "/negotiations", json=payload, auth=True)
        return payload