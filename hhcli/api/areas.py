from __future__ import annotations

from typing import Any

from ..http import request


def get_areas_tree() -> Any:
    return request("GET", "/areas")


def get_area_node(area_id: int) -> Any:
    """Вернуть узел с детьми для указанного area_id (например, 113 = Россия)."""
    return request("GET", f"/areas/{area_id}")
