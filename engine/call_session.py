"""The engine's live in-memory state for the single active Call Session.

The Call Engine owns one call at a time (one booth phone). A ``CallSession``
carries that call's identity from ``StasisStart`` (session id, channel, caller,
start time) and is filled in with the terminal outcome — the mode handler's own
via :meth:`CallSession.complete`, or the one :meth:`CallSession.abandon`
synthesises when the handler never returned — then flattened into a
:class:`~engine.call_store.CallRecord` for the SQLite store. It is deliberately
a plain mutable object the Phase 2 dashboard can read straight off the engine to
render the current call — no persistence or ARI knowledge lives here.

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
# - ``exiled``     the caller lost and got the Exile message: they burned the
#                  Attempt Limit, or walked the Roguelike Phone-Tree's bound out
#                  without finding the room holding the Code (#59).
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
# ``dropped`` is absent on purpose: no mode handler can return it, because it is
# the engine's own failure. :meth:`CallSession.abandon` sets that state and its
# outcome together, as the pair they are.
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
    ``outcome`` / ``attempts`` / ``ended_at`` / ``detail`` stay empty until the
    call ends, through :meth:`complete` or :meth:`abandon`. ``state`` and
    ``digits`` are the live view: where the call has got to, and what the caller
    has dialled.
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

        The handler returned no outcome, so one is synthesised here (#50) — and
        the fork above is exactly the distinction that makes that honest. A
        caller who hung up mid-call ended their Call Session the same way as one
        who sat silent through a read, so it is the same ``hangup``: the route
        the news took to us is not something the history should record. An
        engine failure is nobody's ending, so it gets ``dropped``, which no mode
        handler can return and no count of callers should include.
        """
        self.ended_at = datetime.now(UTC)
        if self.caller_gone:
            self.state, self.outcome = "hung_up", "hangup"
        else:
            self.state, self.outcome = "dropped", "dropped"
        self.attempts = self._attempts_finished()
        # The riddle the caller was abandoning is worth keeping — it is already
        # in hand, off the CallObserver seam. A Walk's path is not: the keys
        # pressed live in the walker, inside the worker thread, and die with the
        # exception. So an abandoned Walk carries its room count in ``attempts``
        # and nothing more.
        self.detail = {"puzzle_id": self.puzzle_id} if self.puzzle_id else {}

    def _attempts_finished(self) -> int:
        """How much of the game the engine can honestly claim the caller played.

        Each branch is the number the Mode's own hangup path would have
        returned had it got that far: rooms walked for a Walk
        (``len(walk["path"])``, which is what ``enter_node`` reports as
        ``depth``), and completed attempts for code entry (``attempt - 1``, an
        attempt in flight being one the caller never burned). Zero when the call
        ended before either — the caller took nothing from the booth.
        """
        if self.node is not None:
            return self.node.depth
        if self.current_attempt is not None:
            return self.current_attempt - 1
        return 0

    @property
    def is_persistable(self) -> bool:
        """Is there an ending here honest enough to write down?

        Both an ending (:meth:`complete` or :meth:`abandon` has run) and a Mode:
        a call that failed before its Config Snapshot never had a game, so there
        is nothing to record the caller as having played. The engine asks before
        writing and :meth:`to_record` refuses when it is false, so the rule is
        stated once rather than agreed on by two places.
        """
        return self.ended_at is not None and self.outcome is not None and self.mode is not None

    def to_record(self) -> CallRecord:
        """Flatten a finished session into a persistable :class:`CallRecord`.

        Duration is the wall-clock span from pickup to terminal outcome. Call
        only after :meth:`complete` or :meth:`abandon`; a session still in
        flight has no ending to write down, and neither has one that failed
        before its Config Snapshot was taken — no Mode means no game to record
        the caller as having played.
        """
        if not self.is_persistable:
            raise ValueError("CallSession has no ending to persist")
        # What is_persistable just established, spelled out for the type checker.
        assert self.ended_at is not None and self.outcome is not None and self.mode is not None
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
