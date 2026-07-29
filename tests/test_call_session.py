"""Tests for CallSession — the engine's live state and its CallRecord mapping."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.call_session import CallSession


def _session(**overrides: object) -> CallSession:
    base: dict[str, object] = {
        "session_id": "sess-1",
        "channel_id": "chan-1",
        "started_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        "caller_id": "+15550001234",
    }
    base.update(overrides)
    return CallSession(**base)  # type: ignore[arg-type]


def test_complete_records_outcome_and_stamps_end() -> None:
    session = _session()
    session.complete({"mode": "tweeted", "outcome": "succeed", "attempts": 1})

    assert session.mode == "tweeted"
    assert session.outcome == "succeed"
    assert session.attempts == 1
    assert session.ended_at is not None
    assert session.ended_at >= session.started_at


def test_complete_keeps_only_nonempty_detail() -> None:
    session = _session()
    # A tweeted result carries an empty path / no puzzle_id — neither should
    # bloat the detail column.
    session.complete({"mode": "tweeted", "outcome": "exile", "attempts": 3, "path": [], "puzzle_id": ""})
    assert session.detail == {}


def test_complete_captures_roguelike_path() -> None:
    session = _session()
    session.complete({"mode": "roguelike", "outcome": "succeed", "attempts": 4, "path": ["1", "2", "1"]})
    assert session.detail == {"path": ["1", "2", "1"]}


def test_complete_captures_puzzle_id() -> None:
    session = _session()
    session.complete({"mode": "puzzle", "outcome": "succeed", "attempts": 1, "puzzle_id": "riddle-001.wav"})
    assert session.detail == {"puzzle_id": "riddle-001.wav"}


def test_to_record_computes_wall_clock_duration() -> None:
    session = _session(started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))
    session.complete({"mode": "tweeted", "outcome": "succeed", "attempts": 1})
    session.ended_at = datetime(2026, 1, 1, 12, 0, 45, tzinfo=UTC)

    record = session.to_record()
    assert record.session_id == "sess-1"
    assert record.caller_id == "+15550001234"
    assert record.mode == "tweeted"
    assert record.outcome == "succeed"
    assert record.attempts == 1
    assert record.duration_seconds == 45.0


def test_to_record_before_complete_raises() -> None:
    with pytest.raises(ValueError, match="not complete"):
        _session().to_record()


def test_hangup_is_a_persistable_outcome() -> None:
    session = _session(caller_id=None)
    session.complete({"mode": "tweeted", "outcome": "hangup", "attempts": 0})
    session.ended_at = datetime(2026, 1, 1, 12, 0, 5, tzinfo=UTC)
    record = session.to_record()
    assert record.outcome == "hangup"
    assert record.caller_id is None
