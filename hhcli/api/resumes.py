from __future__ import annotations

from typing import Any

from hhcli.http import request


def my_resumes() -> dict[str, Any]:
    return request("GET", "/resumes/mine", auth=True)
