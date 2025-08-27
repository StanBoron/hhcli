from __future__ import annotations
from typing import Any

from ..http import request

def get_dictionaries() -> Any:
    """Словари hh (schedule, employment, experience и т.п.)."""
    return request("GET", "/dictionaries")
