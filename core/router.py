"""Evaluate one attempt of a Call Session against its Config Snapshot."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core import mode_puzzle, mode_tweeted
from core.config import ConfigSnapshot
from core.logger import CallSessionLogger
from core.types import Outcome


class Router:
    """Judges a Call Session's attempts against the config it picked up with.

    One Router per Call Session. It is handed a :class:`ConfigSnapshot` taken at
    pickup and never re-reads Global Config, so an Operator rotating the Code or
    switching Mode mid-call cannot change the game the current caller is being
    scored against — the write lands on the next call, which takes its own
    snapshot. (Before #34 the router re-read ``mode.json`` per attempt, and a
    rotation mid-call failed callers for correctly answering the riddle they had
    just been played.)

    **Attempts, and only attempts.** An attempt is a set of digits offered
    against the Code, so this judges the two Modes that have them and refuses
    the Roguelike Phone-Tree, which has none: a Walk's outcome is decided by the
    walker as the caller makes it, and ``core.flow`` builds the record from that
    Walk. Until #56 this branch answered anyway, by simulating a whole fresh
    random walk on a whole fresh tree — so the path and the room count that got
    logged and persisted were a stranger's, and the outcome was ``succeed``
    however the real caller's walk had gone. The logger is still shared, because
    it is the Call Session's, not this method's.
    """

    config: ConfigSnapshot
    logger: CallSessionLogger

    def __init__(self, config: ConfigSnapshot, log_dir: Path) -> None:
        self.config = config
        self.logger = CallSessionLogger(log_dir)

    def dispatch(
        self,
        code_attempt: str | None = None,
        answer: str | None = None,
        attempt: int = 1,
        puzzle_id: str | None = None,
        log: bool = True,
    ) -> dict[str, Any]:
        mode = self.config.mode
        if mode == "roguelike":
            raise ValueError(
                "roguelike walks are not dispatched: the walker decides a Walk's "
                "outcome and core.flow records it (#56)"
            )
        if mode == "puzzle" and not puzzle_id:
            raise ValueError("puzzle_id is required for puzzle mode")

        start = time.monotonic()
        code = self.config.code
        max_attempts = self.config.attempt_limit

        if mode == "tweeted":
            handler_result = mode_tweeted.handle(code_attempt or "", code, attempt, max_attempts)
        else:
            assert puzzle_id is not None
            handler_result = mode_puzzle.handle(
                answer=answer or "",
                expected_code=code,
                attempt=attempt,
                max_attempts=max_attempts,
                puzzle_id=puzzle_id,
            )

        duration = round(time.monotonic() - start, 3)
        outcome: Outcome = handler_result["outcome"]

        session = {
            "timestamp": datetime.now(UTC),
            "mode": mode,
            "outcome": outcome,
            "duration": duration,
            "attempts": handler_result.get("attempts", 0),
        }
        if puzzle_id:
            session["puzzle_id"] = puzzle_id
        if log:
            self.logger.log(session)

        return {
            "mode": mode,
            "outcome": outcome,
            "attempts": handler_result.get("attempts", 0),
            "duration": duration,
            "puzzle_id": handler_result.get("puzzle_id", ""),
        }
