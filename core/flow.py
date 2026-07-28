"""Channel-agnostic interactive call flow.

Each ``run_*`` function drives one Call Session for a mode, talking to the
caller only through a :class:`~core.call_io.CallIO`. The same functions back
both the AGI entry point and the ARI Call Engine; the driver differs, the game
logic does not.

Media identifiers (``prompt_media``, ``exile_media``, ``wrong_media``) are
supplied by the caller because their naming is driver-specific — see
``CallIO``. Terminal outcomes are logged exactly once via the router.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core import mode_roguelike
from core.call_io import CallIO
from core.router import Router

TWEETED_TIMEOUT_MS = 15_000
PUZZLE_TIMEOUT_MS = 30_000
ROGUELIKE_CHOICE_TIMEOUT_MS = 15_000


def run_tweeted(
    io: CallIO,
    router: Router,
    *,
    code: str,
    max_attempts: int,
    exile_media: str,
    wrong_media: str,
    timeout_ms: int = TWEETED_TIMEOUT_MS,
) -> dict[str, Any]:
    """Tweeted mode: the caller dials the published code directly."""
    return _run_code_entry(
        io,
        router,
        digit_count=len(code),
        timeout_ms=timeout_ms,
        max_attempts=max_attempts,
        answer_field="code_attempt",
        extra_dispatch={},
        exile_media=exile_media,
        wrong_media=wrong_media,
    )


def run_puzzle(
    io: CallIO,
    router: Router,
    *,
    code: str,
    max_attempts: int,
    puzzle_id: str,
    prompt_media: str,
    exile_media: str,
    wrong_media: str,
    timeout_ms: int = PUZZLE_TIMEOUT_MS,
) -> dict[str, Any]:
    """Puzzle mode: play the riddle, then collect the answer under an attempt limit."""
    io.play(prompt_media)
    return _run_code_entry(
        io,
        router,
        digit_count=len(code),
        timeout_ms=timeout_ms,
        max_attempts=max_attempts,
        answer_field="answer",
        extra_dispatch={"puzzle_id": puzzle_id},
        exile_media=exile_media,
        wrong_media=wrong_media,
    )


def run_roguelike(
    io: CallIO,
    router: Router,
    *,
    code: str,
    choice_timeout_ms: int = ROGUELIKE_CHOICE_TIMEOUT_MS,
) -> dict[str, Any]:
    """Roguelike mode: navigate the phone-tree, then deliver the code at the leaf."""
    ctx = _RoguelikeCallIOContext(io, choice_timeout_ms)
    mode_roguelike.handle(ctx, code)

    result = router.dispatch()
    if result["outcome"] == "succeed":
        io.to_success()
    else:
        io.hangup()
    return result


def _run_code_entry(
    io: CallIO,
    router: Router,
    *,
    digit_count: int,
    timeout_ms: int,
    max_attempts: int,
    answer_field: str,
    extra_dispatch: dict[str, Any],
    exile_media: str,
    wrong_media: str,
) -> dict[str, Any]:
    """Collect DTMF answers under an attempt limit, dispatching each one.

    The router evaluates every attempt; only the terminal outcome (success or
    exile) is logged, and exactly once. A wrong non-final answer replays
    ``wrong_media``; no input at all is treated as the caller hanging up.
    """
    result: dict[str, Any] = {"outcome": "hangup", "attempts": 0}
    for attempt in range(1, max_attempts + 1):
        entered = io.read_dtmf(digit_count, timeout_ms)
        if not entered:
            io.hangup()
            return {"outcome": "hangup", "attempts": attempt - 1}

        is_last = attempt == max_attempts
        result = router.dispatch(
            attempt=attempt,
            log=is_last,
            **{answer_field: entered, **extra_dispatch},
        )
        outcome = result["outcome"]

        if outcome == "succeed":
            # dispatch(log=is_last) already logged on the final attempt; on an
            # earlier attempt we log the win here so it lands exactly once.
            if not is_last:
                router.logger.log({**result, "timestamp": datetime.now(UTC)})
            io.to_success()
            return result

        if outcome == "exile":
            io.play(exile_media)
            io.hangup()
            return result

        io.play(wrong_media)

    return result


class _RoguelikeCallIOContext:
    """Adapts a :class:`CallIO` to the roguelike navigator's ``ctx`` interface."""

    def __init__(self, io: CallIO, choice_timeout_ms: int) -> None:
        self._io = io
        self._choice_timeout_ms = choice_timeout_ms

    def speak(self, text: str) -> None:
        self._io.speak(text)

    def read_choice(self, keys: str) -> str:
        return self._io.read_dtmf(1, self._choice_timeout_ms)
