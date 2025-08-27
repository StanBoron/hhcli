from __future__ import annotations
from typing import Any

from ..http import request

def get_roles() -> Any:
    """Справочник professional_roles (категории и роли)."""
    return request("GET", "/professional_roles")
