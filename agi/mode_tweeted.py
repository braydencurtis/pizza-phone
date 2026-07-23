from __future__ import annotations

from typing import Any


def handle(code_attempt: str, expected_code: str, attempt: int, max_attempts: int) -> dict[str, Any]:
    if code_attempt == expected_code:
        return {
            "outcome": "succeed",
            "attempts": attempt,
        }
    if attempt >= max_attempts:
        return {
            "outcome": "exile",
            "attempts": attempt,
        }
    return {
        "outcome": "fail",
        "attempts": attempt,
    }
