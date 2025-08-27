from __future__ import annotations
from typing import Any, Dict

from hhcli.http import request

def get_employer(employer_id: str) -> Dict[str, Any]:
    return request("GET", f"/employers/{employer_id}")