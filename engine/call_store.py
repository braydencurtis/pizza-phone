"""SQLite call-history store — the queryable record of every completed Call Session.

Supersedes the JSONL logger (``core/logger.py``). The Operator Console's needs
are query-shaped — filter by mode/outcome/date, paginate, later join calls to
their recordings and hunt clips for video — so history moves into a single-file
SQLite database. Recordings stay as WAV files on disk; the ``recording_*``
columns hold only their paths and are populated in Phase 3.

Access is stdlib :mod:`sqlite3`, not ``aiosqlite`` — a phone booth logs a
handful of calls a day, so the dependency would buy nothing. To keep blocking
file I/O off the engine's event loop, every public method is a coroutine that
runs its query in a worker thread via :func:`asyncio.to_thread`, opening a fresh
connection per call so nothing is shared across threads. (An in-memory
``:memory:`` database therefore won't work — each operation would see an empty
DB; use a file path.)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.types import Mode, Outcome

# Column order shared by the schema, INSERT, and every SELECT — keep the three
# in lock-step with _to_row / _from_row.
_COLUMNS = (
    "session_id",
    "started_at",
    "ended_at",
    "mode",
    "outcome",
    "duration_seconds",
    "attempts",
    "caller_id",
    "detail",
    "recording_mixed",
    "recording_in",
    "recording_out",
)

_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM calls"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    session_id       TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    ended_at         TEXT NOT NULL,
    mode             TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    attempts         INTEGER NOT NULL,
    caller_id        TEXT,
    detail           TEXT NOT NULL DEFAULT '{}',
    recording_mixed  TEXT,
    recording_in     TEXT,
    recording_out    TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_started_at ON calls (started_at);
CREATE INDEX IF NOT EXISTS idx_calls_mode ON calls (mode);
CREATE INDEX IF NOT EXISTS idx_calls_outcome ON calls (outcome);
"""


def new_session_id() -> str:
    """Mint a fresh Call Session id (uuid4 hex) for the ``calls`` primary key."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One completed Call Session, as persisted in the ``calls`` table.

    ``detail`` carries the semi-structured, per-mode extras (roguelike path,
    ``puzzle_id``, nodes visited) as JSON. The ``recording_*`` paths point at
    WAV files on disk and stay ``None`` until Phase 3 wires up recording.
    """

    session_id: str
    started_at: datetime
    ended_at: datetime
    mode: Mode
    outcome: Outcome
    duration_seconds: float
    attempts: int
    caller_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    recording_mixed: str | None = None
    recording_in: str | None = None
    recording_out: str | None = None


class CallStore:
    """Async facade over the ``calls`` SQLite table.

    Construct with the database file path, ``await initialize()`` once at
    engine startup to create the schema, then ``add`` completed sessions and
    ``query`` / ``get`` them back.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)

    async def initialize(self) -> None:
        """Create the ``calls`` table and its indexes if they don't exist."""
        await asyncio.to_thread(self._initialize)

    async def add(self, record: CallRecord) -> None:
        """Persist a completed Call Session. Raises on a duplicate ``session_id``."""
        await asyncio.to_thread(self._add, record)

    async def get(self, session_id: str) -> CallRecord | None:
        """Fetch one session by id, or ``None`` if there is no such row."""
        return await asyncio.to_thread(self._get, session_id)

    async def query(
        self,
        *,
        mode: Mode | None = None,
        outcome: Outcome | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[CallRecord]:
        """Return matching sessions, most recent first.

        ``since`` is inclusive and ``until`` exclusive, both compared against
        ``started_at``. All filters combine with AND; omit one to leave it
        unconstrained.
        """
        return await asyncio.to_thread(
            self._query, mode, outcome, since, until, limit
        )

    # -- worker-thread bodies (each opens its own connection) --------------

    def _initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            conn.executescript(_SCHEMA)

    def _add(self, record: CallRecord) -> None:
        placeholders = ", ".join("?" for _ in _COLUMNS)
        sql = f"INSERT INTO calls ({', '.join(_COLUMNS)}) VALUES ({placeholders})"
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            conn.execute(sql, _to_row(record))

    def _get(self, session_id: str) -> CallRecord | None:
        with closing(sqlite3.connect(self._db_path)) as conn:
            row = conn.execute(
                f"{_SELECT} WHERE session_id = ?", (session_id,)
            ).fetchone()
        return _from_row(row) if row is not None else None

    def _query(
        self,
        mode: Mode | None,
        outcome: Outcome | None,
        since: datetime | None,
        until: datetime | None,
        limit: int | None,
    ) -> list[CallRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if mode is not None:
            clauses.append("mode = ?")
            params.append(mode)
        if outcome is not None:
            clauses.append("outcome = ?")
            params.append(outcome)
        if since is not None:
            clauses.append("started_at >= ?")
            params.append(_utc_iso(since))
        if until is not None:
            clauses.append("started_at < ?")
            params.append(_utc_iso(until))

        sql = _SELECT
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        with closing(sqlite3.connect(self._db_path)) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_from_row(row) for row in rows]


def _utc_iso(dt: datetime) -> str:
    """Serialize an aware datetime as ISO 8601 UTC text.

    Every timestamp is normalized to UTC so stored and query-bound strings
    share one offset and lexical comparison equals chronological comparison
    (date-range filters lean on this). A naive datetime is a bug — the codebase
    deals in aware UTC throughout — so it's rejected rather than silently
    assumed to be local time.
    """
    if dt.tzinfo is None:
        raise ValueError(f"expected a timezone-aware datetime, got naive: {dt!r}")
    return dt.astimezone(UTC).isoformat()


def _to_row(record: CallRecord) -> tuple[Any, ...]:
    """Flatten a record into the column tuple (datetimes → UTC ISO, detail → JSON)."""
    return (
        record.session_id,
        _utc_iso(record.started_at),
        _utc_iso(record.ended_at),
        record.mode,
        record.outcome,
        record.duration_seconds,
        record.attempts,
        record.caller_id,
        json.dumps(record.detail, separators=(",", ":")),
        record.recording_mixed,
        record.recording_in,
        record.recording_out,
    )


def _from_row(row: tuple[Any, ...]) -> CallRecord:
    """Rebuild a record from a ``_COLUMNS``-ordered row."""
    return CallRecord(
        session_id=row[0],
        started_at=datetime.fromisoformat(row[1]),
        ended_at=datetime.fromisoformat(row[2]),
        mode=row[3],
        outcome=row[4],
        duration_seconds=row[5],
        attempts=row[6],
        caller_id=row[7],
        detail=json.loads(row[8]),
        recording_mixed=row[9],
        recording_in=row[10],
        recording_out=row[11],
    )
