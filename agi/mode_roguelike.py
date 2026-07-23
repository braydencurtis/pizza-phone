from __future__ import annotations

from typing import Any


def handle(path: list[str], _code: str) -> dict[str, Any]:
    if not path:
        return {
            "outcome": "succeed",
            "attempts": 0,
            "path": [],
        }
    return {
        "outcome": "succeed",
        "attempts": 0,
        "path": path,
    }
