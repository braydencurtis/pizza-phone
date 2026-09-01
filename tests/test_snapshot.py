"""Tests for the console telemetry snapshot builder (ticket #35).

The console is fed *whole-state* snapshots — Global Config plus the live Call
Session, or an idle marker when no call is in progress — never deltas. The
builder is pure: it turns the two in-memory objects into a JSON-ready dict, so
these tests need no engine, no ARI, and no server.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.config import ConfigSnapshot
from core.types import Mode, Outcome
from engine.call_session import CallSession
from engine.snapshot import SNAPSHOT_SCHEMA_VERSION, build_snapshot

STARTED_AT = datetime(2026, 8, 27, 12, 30, 0, tzinfo=UTC)


def _config() -> ConfigSnapshot:
    return ConfigSnapshot(mode="puzzle", code="1234", attempt_limit=5, upstream_extension="300")


def _session(
    mode: Mode | None = "puzzle",
    config: ConfigSnapshot | None = None,
    caller_id: str | None = "+15551234567",
    attempts: int = 2,
    outcome: Outcome | None = None,
) -> CallSession:
    return CallSession(
        session_id="sess-1",
        channel_id="chan-1",
        started_at=STARTED_AT,
        caller_id=caller_id,
        config=config,
        mode=mode,
        attempts=attempts,
        outcome=outcome,
    )


def test_schema_version_is_one() -> None:
    assert SNAPSHOT_SCHEMA_VERSION == 1


def test_idle_snapshot_has_no_call() -> None:
    snap = build_snapshot(_config(), None)
    assert snap["schema"] == SNAPSHOT_SCHEMA_VERSION
    assert snap["call"] is None


def test_snapshot_carries_the_global_config_view() -> None:
    snap = build_snapshot(_config(), None)
    assert snap["config"] == {
        "mode": "puzzle",
        "code": "1234",
        "attempt_limit": 5,
        "upstream_extension": "300",
    }


def test_live_snapshot_carries_the_call() -> None:
    snap = build_snapshot(_config(), _session(config=_config()))
    call = snap["call"]
    assert call["session_id"] == "sess-1"
    assert call["mode"] == "puzzle"
    assert call["caller_id"] == "+15551234567"
    assert call["started_at"] == STARTED_AT.isoformat()
    assert call["attempts"] == 2
    assert call["outcome"] is None


def test_call_mode_falls_back_to_the_config_snapshot_when_unstamped() -> None:
    snap = build_snapshot(_config(), _session(mode=None, config=_config()))
    assert snap["call"]["mode"] == "puzzle"


def test_call_mode_is_null_when_no_mode_is_recorded_anywhere() -> None:
    snap = build_snapshot(_config(), _session(mode=None, config=None))
    assert snap["call"]["mode"] is None


def test_stamped_mode_wins_over_the_config_snapshot() -> None:
    snap = build_snapshot(_config(), _session(mode="roguelike", config=_config()))
    assert snap["call"]["mode"] == "roguelike"


def test_call_view_keeps_a_null_caller_id() -> None:
    snap = build_snapshot(_config(), _session(caller_id=None, config=_config()))
    assert snap["call"]["caller_id"] is None


def test_completed_call_carries_its_outcome() -> None:
    snap = build_snapshot(_config(), _session(outcome="succeed", config=_config()))
    assert snap["call"]["outcome"] == "succeed"
