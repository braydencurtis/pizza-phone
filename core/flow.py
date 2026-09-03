"""Channel-agnostic interactive call flow.

Each ``run_*`` function drives one Call Session for a mode, talking to the
caller only through a :class:`~core.call_io.CallIO`. The same functions backed
the AGI entry point and now the ARI Call Engine; the driver differs, the game
logic does not.

The Code and the Attempt Limit are not parameters: they come from the
``Router``'s Config Snapshot, so the digits collected and the digits judged are
always the same call's config even if the Operator rotates the Code mid-call.
Media identifiers (``prompt_media``, ``exile_media``, ``wrong_media``) are
supplied by the caller because their naming is driver-specific — see
``CallIO``. Terminal outcomes are logged exactly once via the router.

The maze is the one Mode a caller can lose without getting anything wrong: a
walk that runs out its bound without finding the room ends in Exile, where it
used to be handed the Code anyway (#59). It is also the one Mode the ``Router``
does not judge, because it has no attempts to judge — the walker settles the
outcome as the caller walks, and ``_walk_played`` writes that Walk down (#56).

Silence means the same thing in all three Modes: an empty ``read_dtmf`` is the
caller having gone, so the call is torn down and the session ends on
``hangup``. It is the caller's commonest ending — a handset put down, or left
off the hook — and the booth holds one call at a time, so a Mode that kept
asking an empty booth would hang up on everybody behind it (#53). The maze
reaches the same ending by one further route, since a handset can be put down
badly as well as quietly: a key it holds down that the room never offers is
nobody choosing, and the walker gives up on it (#55).

Each function also takes an optional :class:`~core.observer.CallObserver` (#37).
These functions return exactly once, at the terminal outcome, so without it
nothing about a call in progress — which attempt, which room, which riddle —
ever escapes. It defaults to a do-nothing observer, so `core/` stays usable and
testable with none.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from core import mode_roguelike
from core.call_io import CallIO
from core.observer import NULL_OBSERVER, CallObserver
from core.router import Router

TWEETED_TIMEOUT_MS = 15_000
PUZZLE_TIMEOUT_MS = 30_000
ROGUELIKE_CHOICE_TIMEOUT_MS = 15_000


def run_tweeted(
    io: CallIO,
    router: Router,
    *,
    exile_media: str,
    wrong_media: str,
    timeout_ms: int = TWEETED_TIMEOUT_MS,
    observer: CallObserver = NULL_OBSERVER,
) -> dict[str, Any]:
    """Tweeted mode: the caller dials the published code directly."""
    return _run_code_entry(
        io,
        router,
        timeout_ms=timeout_ms,
        answer_field="code_attempt",
        extra_dispatch={},
        exile_media=exile_media,
        wrong_media=wrong_media,
        observer=observer,
    )


def run_puzzle(
    io: CallIO,
    router: Router,
    *,
    puzzle_id: str,
    prompt_media: str,
    exile_media: str,
    wrong_media: str,
    timeout_ms: int = PUZZLE_TIMEOUT_MS,
    observer: CallObserver = NULL_OBSERVER,
) -> dict[str, Any]:
    """Puzzle mode: play the riddle, then collect the answer under an attempt limit."""
    # Announced before the prompt plays: the Operator should know which riddle
    # is in the caller's ear while it is playing, not once they answer it.
    observer.puzzle_selected(puzzle_id)
    io.play(prompt_media)
    return _run_code_entry(
        io,
        router,
        timeout_ms=timeout_ms,
        answer_field="answer",
        extra_dispatch={"puzzle_id": puzzle_id},
        exile_media=exile_media,
        wrong_media=wrong_media,
        observer=observer,
    )


def run_roguelike(
    io: CallIO,
    router: Router,
    *,
    choice_timeout_ms: int = ROGUELIKE_CHOICE_TIMEOUT_MS,
    seed: int | None = None,
    observer: CallObserver = NULL_OBSERVER,
) -> dict[str, Any]:
    """Roguelike mode: navigate the phone-tree, and win only by finding the room.

    Three endings, all of them the walker's (``mode_roguelike.Walk``), and the
    record is that Walk whichever one it reached — the caller's own keys and
    rooms, never a simulation's (#56). Reaching the leaf is the win. Walking the
    bound out without finding it is Exile, and is a call the caller *played and
    lost* — so like the win and unlike the third ending it is logged (#59). A
    caller who stops choosing — silent, or holding a key the room does not offer
    — has gone, exactly as in the other two Modes (#53, #55); that arrives here
    as a walk nobody played.

    ``seed`` pins the tree, which is otherwise regenerated per Call Session.
    Nothing in the engine passes one — it is how a test gets a walk it can
    predict, the same reason ``mode_roguelike.handle`` takes one, and it is
    needed here because which of the three endings a walk reaches is otherwise
    a roll of the dice.
    """
    ctx = _RoguelikeCallIOContext(io, choice_timeout_ms)
    started = time.monotonic()
    walk = mode_roguelike.handle(ctx, router.config.code, seed=seed, observer=observer)

    if walk["outcome"] == "hangup":
        # Not recorded as a session and not logged, exactly as in
        # ``_run_code_entry``: nobody played, so there is nothing to write down
        # as a game — whether they went silent or spent the call on a key the
        # room does not offer. The engine still persists the call as a hangup,
        # and the rooms the caller got through before leaving go with it — how
        # far they got is the interesting part of a walk that was abandoned.
        return _caller_left(
            io,
            mode=router.config.mode,
            attempts=mode_roguelike.moves_made(walk),
            path=walk["path"],
        )

    return _walk_played(io, router, walk, round(time.monotonic() - started, 3))


def _run_code_entry(
    io: CallIO,
    router: Router,
    *,
    timeout_ms: int,
    answer_field: str,
    extra_dispatch: dict[str, Any],
    exile_media: str,
    wrong_media: str,
    observer: CallObserver = NULL_OBSERVER,
) -> dict[str, Any]:
    """Collect DTMF answers under an attempt limit, dispatching each one.

    How many digits to collect and how many tries the caller gets both come from
    the router's Config Snapshot, so the caller is judged against the same Code
    whose length they were asked to dial.

    The router evaluates every attempt; only the terminal outcome (success or
    exile) is logged, and exactly once. A wrong non-final answer replays
    ``wrong_media``; no input at all is treated as the caller hanging up.
    """
    digit_count = len(router.config.code)
    max_attempts = router.config.attempt_limit
    result: dict[str, Any] = {"outcome": "hangup", "attempts": 0}
    for attempt in range(1, max_attempts + 1):
        observer.attempt_started(attempt, max_attempts)
        entered = io.read_dtmf(digit_count, timeout_ms)
        if not entered:
            # An attempt the caller never made is not one they burned.
            return _caller_left(io, attempts=attempt - 1)

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


def _walk_played(
    io: CallIO, router: Router, walk: mode_roguelike.Walk, duration: float
) -> dict[str, Any]:
    """The caller played the maze to one of its two endings: record what they did.

    Both endings come through here, and the record is the walk — the keys they
    pressed, the rooms they stood in, and which ending the walker gave them.
    The win used to go through ``Router.dispatch`` instead, which for roguelike
    simulated a fresh random walk on a fresh tree and reported *that*: a won
    call was persisted under a stranger's rooms, and was persisted as a win
    however the caller's own walk had gone, because the simulation always
    reached a leaf (#56). Nothing here is judged, because there is nothing left
    to judge — the walker settled the outcome as the caller walked.

    Logged, where ``_caller_left`` is not, and that is the whole distinction
    between them: this caller played the game, where the other walked away from
    it. ``attempts`` is ``mode_roguelike.moves_made`` — the same derivation
    ``_caller_left`` uses for an abandoned walk, because it is the same number:
    the Roguelike Phone-Tree has no attempts of the kind the other two Modes
    mean, so the column carries moves instead and the Console labels it Rooms
    (CONTEXT.md, *A roguelike record*).
    """
    result: dict[str, Any] = {
        "mode": router.config.mode,
        "outcome": walk["outcome"],
        "attempts": mode_roguelike.moves_made(walk),
        "duration": duration,
        "path": walk["path"],
    }
    # ``nodes_visited`` is logged but not returned: the rooms are for the
    # history, and the engine has no use for them mid-call — the Console
    # already watched the caller enter each one on the CallObserver seam.
    router.logger.log(
        {**result, "nodes_visited": walk["nodes_visited"], "timestamp": datetime.now(UTC)}
    )
    if walk["outcome"] == "succeed":
        io.to_success()
    else:
        io.hangup()
    return result


def _caller_left(io: CallIO, **detail: Any) -> dict[str, Any]:
    """The caller stopped playing: tear the call down and end the session on ``hangup``.

    The one place a caller's absence becomes an outcome, so the rule CONTEXT.md
    states — silence ends a Call Session in every Mode — is one rule rather than
    three that happen to agree. The maze reaches it by one further route, a key
    it can only read as nobody choosing (#55). What each Mode knows about the
    abandoned call differs (attempts burned, rooms walked), so that rides along
    in ``detail``.
    """
    io.hangup()
    return {"outcome": "hangup", **detail}


class _RoguelikeCallIOContext:
    """Adapts a :class:`CallIO` to the roguelike navigator's ``ctx`` interface."""

    def __init__(self, io: CallIO, choice_timeout_ms: int) -> None:
        self._io = io
        self._choice_timeout_ms = choice_timeout_ms

    def speak(self, text: str) -> None:
        self._io.speak(text)

    def read_choice(self, keys: str) -> str:
        return self._io.read_dtmf(1, self._choice_timeout_ms)
