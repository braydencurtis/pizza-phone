"""The engine's live in-memory state for the single active Call Session.

The Call Engine owns one call at a time (one booth phone). A ``CallSession``
carries that call's identity from ``StasisStart`` (session id, channel, caller,
start time) and is filled in with the terminal outcome once the mode handler
returns, then flattened into a :class:`~engine.call_store.CallRecord` for the
SQLite store. It is deliberately a plain mutable object the Phase 2 dashboard
can read straight off the engine to render the current call — no persistence or
ARI knowledge lives here.

**The state vocabulary** (#36) is what the Console renders the call *as*:
``answering`` → ``in_mode`` → one of the terminal states. The terminal states
are kept apart on purpose. **Handed Off is a win, not an ending**: the channel
has left the Call Engine for the success dialplan, so the Upstairs Phone
ringing, the Operator answering and the conversation that follows are all
invisible from here. If a win rendered identically to a hangup the Operator
would learn to distrust the panel, so the two never share a state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from core.config import ConfigSnapshot
from core.types import Mode, Outcome
from engine.call_store import CallRecord

# How a Call Session reads on the Console, from pickup to the end.
#
# - ``answering``  the channel is in Stasis, being answered; the Mode is stamped
#                  here, as soon as the Config Snapshot is taken.
# - ``in_mode``    the mode handler is running: the caller is being played to
#                  and is dialling.
# - ``handed_off`` the caller won and left for the success dialplan (see above).
# - ``exiled``     the caller burned the Attempt Limit and got the Exile message.
# - ``hung_up``    the call ended without a win and without Exile.
# - ``dropped``    the *engine* ended the call — a failure upstairs, not
#                  anything the caller did. Kept apart from ``hung_up`` for the
#                  same reason ``handed_off`` is: the Operator should not have
#                  to guess which of the two they are looking at.
CallState = Literal["answering", "in_mode", "handed_off", "exiled", "hung_up", "dropped"]

TERMINAL_STATES: frozenset[CallState] = frozenset({"handed_off", "exiled", "hung_up", "dropped"})

# The terminal state each mode-handler outcome lands the call in. ``fail`` is a
# roguelike walk that reached no leaf; the caller experiences it as the line
# going dead, exactly like ``hangup``, so it is not given a state of its own.
_STATE_BY_OUTCOME: dict[Outcome, CallState] = {
    "succeed": "handed_off",
    "exile": "exiled",
    "hangup": "hung_up",
    "fail": "hung_up",
}

# How many dialled digits the live view keeps. A roguelike walk is an infinite
# maze, so the digit stream has no natural end — the Console wants the digits
# arriving *now*, and the whole history of the call is in the store afterwards.
MAX_LIVE_DIGITS = 16


@dataclass(frozen=True)
class MazePosition:
    """Where a caller stands in the Roguelike Phone-Tree.

    ``depth`` is the readable half — rooms walked through — and ``index`` is a
    coordinate on a map only this call has, since the tree is regenerated per
    Call Session. ``terminal`` is the leaf, where the Code is read aloud.
    """

    index: int
    depth: int
    terminal: bool


@dataclass
class CallSession:
    """One live Call Session, from pickup to terminal outcome.

    ``config`` is the Config Snapshot taken at pickup — the game this caller was
    given, and what they are judged against for the whole call however the
    Operator changes Global Config meanwhile. ``mode`` is stamped from it;
    ``outcome`` / ``attempts`` / ``ended_at`` / ``detail`` stay empty until
    :meth:`complete` records the handler's result. ``state`` and ``digits`` are
    the live view: where the call has got to, and what the caller has dialled.
    """

    session_id: str
    channel_id: str
    started_at: datetime
    caller_id: str | None = None
    config: ConfigSnapshot | None = None
    mode: Mode | None = None
    state: CallState = "answering"
    digits: list[str] = field(default_factory=list)
    caller_gone: bool = False
    # The live progress the CallObserver reports mid-call (#37). ``attempts``
    # below is the *final* count, learned only when the handler returns;
    # ``current_attempt`` is where the caller is right now. The two are
    # deliberately separate — a cockpit reading the final count mid-call would
    # show a permanent 0 and then jump.
    current_attempt: int | None = None
    attempt_limit: int | None = None
    node: MazePosition | None = None
    puzzle_id: str | None = None
    outcome: Outcome | None = None
    attempts: int = 0
    ended_at: datetime | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_over(self) -> bool:
        """Has this call reached a terminal state?

        The engine keeps a finished session in view for a moment so the Operator
        can read how it ended, so "there is a session" and "somebody is on the
        phone" are not the same question.
        """
        return self.state in TERMINAL_STATES

    def enter_mode(self) -> None:
        """The channel is answered and the mode handler is about to run."""
        self.state = "in_mode"

    def begin_attempt(self, attempt: int, limit: int) -> None:
        """The caller is being asked for their ``attempt``-th answer.

        The limit is stored rather than read back off ``config`` so the Console
        shows the number the flow actually judged against, not one re-derived
        from a snapshot that might drift from it.
        """
        self.current_attempt = attempt
        self.attempt_limit = limit

    def enter_node(self, index: int, depth: int, terminal: bool) -> None:
        """The caller has walked into a room of the Roguelike Phone-Tree."""
        self.node = MazePosition(index=index, depth=depth, terminal=terminal)

    def select_puzzle(self, puzzle_id: str) -> None:
        """This Call Session drew ``puzzle_id`` from the Puzzle Pool."""
        self.puzzle_id = puzzle_id

    def record_digit(self, digit: str) -> None:
        """Note a DTMF digit as the caller dials it, oldest dropped past the cap."""
        self.digits.append(digit)
        if len(self.digits) > MAX_LIVE_DIGITS:
            del self.digits[: -MAX_LIVE_DIGITS]

    def complete(self, result: dict[str, Any]) -> None:
        """Record a mode handler's terminal result and stamp the end time.

        ``result`` is the dict ``core.flow`` returns (``mode`` / ``outcome`` /
        ``attempts`` / ``path`` / ``puzzle_id``). The per-mode extras land in
        ``detail`` — the store's JSON column — and only non-empty ones are kept
        so a tweeted call doesn't carry an empty ``path``. The outcome also
        picks the terminal state the Console renders.
        """
        self.ended_at = datetime.now(UTC)
        self.mode = result.get("mode", self.mode)
        self.outcome = result["outcome"]
        self.attempts = result.get("attempts", 0)
        self.state = _STATE_BY_OUTCOME[self.outcome]

        detail: dict[str, Any] = {}
        if result.get("path"):
            detail["path"] = result["path"]
        if result.get("puzzle_id"):
            detail["puzzle_id"] = result["puzzle_id"]
        self.detail = detail

    def abandon(self) -> None:
        """The call ended without the mode handler returning an outcome.

        Which of two very different things happened is the whole point of this
        method. If Asterisk told us the channel went away, the caller put the
        handset down — the commonest ending there is, and nothing to do with us.
        Otherwise the engine broke, and the Console says *that* rather than
        letting our failure pass for a caller walking away.

        Either way there is no outcome to persist (the handler never returned
        one), so this is a Console state only.
        """
        self.ended_at = datetime.now(UTC)
        self.state = "hung_up" if self.caller_gone else "dropped"

    def to_record(self) -> CallRecord:
        """Flatten a completed session into a persistable :class:`CallRecord`.

        Duration is the wall-clock span from pickup to terminal outcome. Call
        only after :meth:`complete`; an unfinished session has no outcome to
        persist.
        """
        if self.ended_at is None or self.outcome is None or self.mode is None:
            raise ValueError("CallSession is not complete; call complete() first")
        duration = (self.ended_at - self.started_at).total_seconds()
        return CallRecord(
            session_id=self.session_id,
            started_at=self.started_at,
            ended_at=self.ended_at,
            mode=self.mode,
            outcome=self.outcome,
            duration_seconds=duration,
            attempts=self.attempts,
            caller_id=self.caller_id,
            detail=self.detail,
        )
