from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agi.mode_puzzle as mode_puzzle
import agi.mode_roguelike as mode_roguelike
import agi.mode_tweeted as mode_tweeted
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
    ) -> dict[str, Any]:
        self.load_config()
        mode: str = self.config.get("mode", "tweeted")
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown mode: {mode!r}")

        start = time.monotonic()
        code: str = self.config.get("code", "0000")
        max_attempts: int = self.config.get("attempt_limit", 3)

        if mode == "tweeted":
            handler_result = mode_tweeted.handle(code_attempt or "", code, 1, max_attempts)
        elif mode == "puzzle":
            handler_result = mode_puzzle.handle(answer or "", code)
        else:
            handler_result = mode_roguelike.handle(path or [], code)

        duration = round(time.monotonic() - start, 3)
        outcome: Outcome = handler_result["outcome"]

        session = {
            "timestamp": datetime.now(timezone.utc),
            "mode": mode,
            "outcome": outcome,
            "duration": duration,
            "attempts": handler_result.get("attempts", 0),
            "path": handler_result.get("path", []),
        }
        self.logger.log(session)

        return {
            "mode": mode,
            "outcome": outcome,
            "attempts": handler_result.get("attempts", 0),
            "duration": duration,
            "path": handler_result.get("path", []),
        }
