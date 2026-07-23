from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agi import mode_puzzle, mode_tweeted
from agi.headless import run_roguelike
from agi.logger import CallSessionLogger
from agi.types import Mode, Outcome

VALID_MODES: list[Mode] = ["tweeted", "puzzle", "roguelike"]


class Router:

    config_path: Path
    config: dict[str, Any]
    logger: CallSessionLogger

    def __init__(self, config_dir: Path, log_dir: Path) -> None:
        self.config_path = config_dir / "mode.json"
        self.logger = CallSessionLogger(log_dir)
        self.config = {}

    def load_config(self) -> dict[str, Any]:
        self.config = json.loads(self.config_path.read_text())
        return self.config

    def dispatch(
        self,
        code_attempt: str | None = None,
        answer: str | None = None,
        path: list[str] | None = None,
        attempt: int = 1,
        puzzle_id: str | None = None,
    ) -> dict[str, Any]:
        self.load_config()
        mode: str = self.config.get("mode", "tweeted")
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown mode: {mode!r}")

        start = time.monotonic()
        code: str = self.config.get("code", "0000")
        max_attempts: int = self.config.get("attempt_limit", 3)

        if mode == "tweeted":
            handler_result = mode_tweeted.handle(code_attempt or "", code, attempt, max_attempts)
        elif mode == "puzzle":
            handler_result = mode_puzzle.handle(
                answer=answer or "",
                expected_code=code,
                attempt=attempt,
                max_attempts=max_attempts,
                puzzle_id=puzzle_id or "",
            )
        else:
            handler_result = run_roguelike(code)

        duration = round(time.monotonic() - start, 3)
        outcome: Outcome = handler_result["outcome"]

        session = {
            "timestamp": datetime.now(UTC),
            "mode": mode,
            "outcome": outcome,
            "duration": duration,
            "attempts": handler_result.get("attempts", 0),
            "path": handler_result.get("path", []),
        }
        if puzzle_id:
            session["puzzle_id"] = puzzle_id
        self.logger.log(session)

        return {
            "mode": mode,
            "outcome": outcome,
            "attempts": handler_result.get("attempts", 0),
            "duration": duration,
            "path": handler_result.get("path", []),
            "puzzle_id": handler_result.get("puzzle_id", ""),
        }
