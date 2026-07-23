from __future__ import annotations

from typing import Any


def handle(answer: str, expected_code: str) -> dict[str, Any]:
    if answer == expected_code:
        return {
            "outcome": "succeed",
            "attempts": 1,
        }
    return {
        "outcome": "fail",
        "attempts": 1,
    }
