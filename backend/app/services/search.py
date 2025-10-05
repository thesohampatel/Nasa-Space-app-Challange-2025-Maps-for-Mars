from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from ..utils import finding_image


def search(scene: str, limit: int = 20) -> Dict[str, Any]:
    entries = finding_image.search_index(scene, limit=limit)
    return {
        "query": scene,
        "count": len(entries),
        "items": [asdict(entry) for entry in entries],
    }


__all__ = ["search"]
