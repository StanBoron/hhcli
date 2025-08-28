from __future__ import annotations

from typing import Any

from hhcli.http import request


def get_employer(employer_id: str) -> dict[str, Any]:
    return request("GET", f"/employers/{employer_id}")
