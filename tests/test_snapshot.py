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
from engine.call_session import CallSession, CallState
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
    state: CallState = "in_mode",
    digits: list[str] | None = None,
) -> CallSession:
    return CallSession(
        session_id="sess-1",
        channel_id="chan-1",
        started_at=STARTED_AT,
        caller_id=caller_id,
        config=config,
        mode=mode,
        state=state,
        digits=digits if digits is not None else [],
        attempts=attempts,
        outcome=outcome,
    )


def test_schema_version_is_two() -> None:
    """#36 added the state vocabulary and the live digits to the call view."""
    assert SNAPSHOT_SCHEMA_VERSION == 2


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


# -- the live Call Session (#36) ----------------------------------------------


def test_the_call_view_carries_its_state() -> None:
    snap = build_snapshot(_config(), _session(state="answering", config=_config()))
    assert snap["call"]["state"] == "answering"


def test_a_live_call_carries_a_start_time_and_no_end() -> None:
    """The browser advances the clock; a live call has no duration to send."""
    snap = build_snapshot(_config(), _session(config=_config()))
    call = snap["call"]
    assert call["started_at"] == STARTED_AT.isoformat()
    assert call["ended_at"] is None
    assert "duration" not in call


def test_an_ended_call_carries_the_moment_it_ended() -> None:
    """So the elapsed timer freezes where the call stopped, not where now is."""
    session = _session(config=_config())
    session.complete({"mode": "puzzle", "outcome": "succeed", "attempts": 1})
    assert session.ended_at is not None
    snap = build_snapshot(_config(), session)
    assert snap["call"]["ended_at"] == session.ended_at.isoformat()


def test_dialled_digits_reach_the_console_as_they_arrive() -> None:
    snap = build_snapshot(_config(), _session(digits=["1", "2", "3"], config=_config()))
    assert snap["call"]["digits"] == "123"


def test_a_caller_who_has_dialled_nothing_has_no_digits() -> None:
    snap = build_snapshot(_config(), _session(config=_config()))
    assert snap["call"]["digits"] == ""


def test_each_terminal_state_is_distinguishable() -> None:
    """A win, an Exile and a hangup must never render as the same thing."""
    states = set()
    for outcome in ("succeed", "exile", "hangup"):
        session = _session(config=_config())
        session.complete({"mode": "tweeted", "outcome": outcome, "attempts": 1})
        states.add(build_snapshot(_config(), session)["call"]["state"])
    assert states == {"handed_off", "exiled", "hung_up"}


def test_a_win_is_handed_off_in_the_snapshot() -> None:
    session = _session(config=_config())
    session.complete({"mode": "tweeted", "outcome": "succeed", "attempts": 1})
    call = build_snapshot(_config(), session)["call"]
    assert call["state"] == "handed_off"
    assert call["outcome"] == "succeed"


def test_a_dropped_call_says_so_rather_than_passing_for_a_hangup() -> None:
    session = _session(config=_config())
    session.abandon()
    call = build_snapshot(_config(), session)["call"]
    assert call["state"] == "dropped"
    assert call["outcome"] is None
