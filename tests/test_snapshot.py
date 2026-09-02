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


def test_schema_version_is_four() -> None:
    """#50 gave the abandoned endings an outcome, `dropped` among them.

    The number is asserted rather than merely mirrored so that widening the wire
    shape without bumping it fails here, where the browser's own
    `SNAPSHOT_SCHEMA_VERSION` is the thing that would otherwise silently drift.
    """
    assert SNAPSHOT_SCHEMA_VERSION == 4


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
    # The synthesised outcome the store gets (#50) reaches the panel too, and
    # says the same thing the state does rather than the caller's "hangup".
    assert call["outcome"] == "dropped"


def test_a_caller_who_hung_up_mid_call_shows_the_outcome_it_is_stored_under() -> None:
    """The panel and the past-calls view must not describe one call two ways."""
    session = _session(config=_config())
    session.caller_gone = True
    session.abandon()
    call = build_snapshot(_config(), session)["call"]
    assert call["state"] == "hung_up"
    assert call["outcome"] == "hangup"


# -- live progress off the CallObserver seam (#37) ---------------------------


def _config_limited(attempt_limit: int) -> ConfigSnapshot:
    """Global Config with a particular Attempt Limit, for the two-limits test."""
    return ConfigSnapshot(
        mode="puzzle", code="1234", attempt_limit=attempt_limit, upstream_extension="300"
    )


def test_a_live_call_carries_the_attempt_it_is_on() -> None:
    """The live counter and the final count are different numbers.

    `attempt` is where the caller is now; `attempts` is what the handler
    returned, and stays 0 until it does. The Console shows one during the call
    and the other after it.
    """
    session = _session(attempts=0)
    session.begin_attempt(2, 3)
    call = build_snapshot(_config(), session)["call"]
    assert call["attempt"] == 2
    assert call["attempt_limit"] == 3
    assert call["attempts"] == 0


def test_the_attempt_limit_shown_is_the_one_this_call_is_judged_against() -> None:
    """Not Global Config's, which an Operator may have changed mid-call.

    The booth is set to 1 now; the caller on the line picked up when it was 5
    and is being judged against 5. Showing the top bar's number on the call
    panel would tell the Operator this caller is one wrong answer from Exile
    when they in fact have four left.
    """
    session = _session()
    session.begin_attempt(1, 5)
    snapshot = build_snapshot(_config_limited(1), session)
    assert snapshot["config"]["attempt_limit"] == 1
    assert snapshot["call"]["attempt_limit"] == 5


def test_the_limit_is_known_before_the_first_attempt() -> None:
    """"Attempt — of 4" beats "attempt — of —" while the call is answering."""
    session = _session(config=_config_limited(4))
    call = build_snapshot(_config(), session)["call"]
    assert call["attempt"] is None
    assert call["attempt_limit"] == 4


def test_a_roguelike_call_carries_the_room_the_caller_is_in() -> None:
    session = _session()
    session.enter_node(7, 3, False)
    call = build_snapshot(_config(), session)["call"]
    assert call["node"] == {"index": 7, "depth": 3, "terminal": False}

    session.enter_node(5, 4, True)
    call = build_snapshot(_config(), session)["call"]
    assert call["node"]["terminal"] is True


def test_a_puzzle_call_carries_the_riddle_the_caller_got() -> None:
    session = _session()
    session.select_puzzle("riddle-07.wav")
    assert build_snapshot(_config(), session)["call"]["puzzle_id"] == "riddle-07.wav"


def test_a_call_with_no_progress_yet_carries_none_of_it() -> None:
    """A tweeted call has no maze and no riddle; it must not invent either."""
    call = build_snapshot(_config(), _session())["call"]
    assert call["attempt"] is None
    assert call["node"] is None
    assert call["puzzle_id"] is None
