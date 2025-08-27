from __future__ import annotations
from typing import Any, Dict
from hhcli.http import request

def my_resumes() -> Dict[str, Any]:
    return request("GET", "/resumes/mine", auth=True)