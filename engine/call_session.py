"""The engine's live in-memory state for the single active Call Session.

The Call Engine owns one call at a time (one booth phone). A ``CallSession``
carries that call's identity from ``StasisStart`` (session id, channel, caller,
start time) and is filled in with the terminal outcome once the mode handler
returns, then flattened into a :class:`~engine.call_store.CallRecord` for the
SQLite store. It is deliberately a plain mutable object the Phase 2 dashboard
can read straight off the engine to render the current call — no persistence or
ARI knowledge lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.config import ConfigSnapshot
from core.types import Mode, Outcome
from engine.call_store import CallRecord


@dataclass
class CallSession:
    """One live Call Session, from pickup to terminal outcome.

    ``config`` is the Config Snapshot taken at pickup — the game this caller was
    given, and what they are judged against for the whole call however the
    Operator changes Global Config meanwhile. ``mode`` is stamped from it;
    ``outcome`` / ``attempts`` / ``ended_at`` / ``detail`` stay empty until
    :meth:`complete` records the handler's result.
    """

    session_id: str
    channel_id: str
    started_at: datetime
    caller_id: str | None = None
    config: ConfigSnapshot | None = None
    mode: Mode | None = None
    outcome: Outcome | None = None
    attempts: int = 0
    ended_at: datetime | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def complete(self, result: dict[str, Any]) -> None:
        """Record a mode handler's terminal result and stamp the end time.

        ``result`` is the dict ``core.flow`` returns (``mode`` / ``outcome`` /
        ``attempts`` / ``path`` / ``puzzle_id``). The per-mode extras land in
        ``detail`` — the store's JSON column — and only non-empty ones are kept
        so a tweeted call doesn't carry an empty ``path``.
        """
        self.ended_at = datetime.now(UTC)
        self.mode = result.get("mode", self.mode)
        self.outcome = result["outcome"]
        self.attempts = result.get("attempts", 0)

        detail: dict[str, Any] = {}
        if result.get("path"):
            detail["path"] = result["path"]
        if result.get("puzzle_id"):
            detail["puzzle_id"] = result["puzzle_id"]
        self.detail = detail

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
