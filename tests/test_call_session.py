"""Tests for CallSession — the engine's live state and its CallRecord mapping."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.call_session import MAX_LIVE_DIGITS, CallSession


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
    with pytest.raises(ValueError, match="no ending"):
        _session().to_record()


def test_hangup_is_a_persistable_outcome() -> None:
    session = _session(caller_id=None)
    session.complete({"mode": "tweeted", "outcome": "hangup", "attempts": 0})
    session.ended_at = datetime(2026, 1, 1, 12, 0, 5, tzinfo=UTC)
    record = session.to_record()
    assert record.outcome == "hangup"
    assert record.caller_id is None


# -- the live state vocabulary (#36) ------------------------------------------


def test_a_new_session_is_answering() -> None:
    assert _session().state == "answering"
    assert not _session().is_over


def test_entering_the_mode_moves_the_state_on() -> None:
    session = _session()
    session.enter_mode()
    assert session.state == "in_mode"
    assert not session.is_over


def test_a_win_is_handed_off_not_a_hangup() -> None:
    """The one distinction the panel exists to make."""
    session = _session()
    session.complete({"mode": "tweeted", "outcome": "succeed", "attempts": 1})
    assert session.state == "handed_off"
    assert session.is_over


def test_exhausting_the_attempt_limit_is_exiled() -> None:
    session = _session()
    session.complete({"mode": "tweeted", "outcome": "exile", "attempts": 3})
    assert session.state == "exiled"


def test_a_caller_who_says_nothing_hung_up() -> None:
    session = _session()
    session.complete({"mode": "tweeted", "outcome": "hangup", "attempts": 0})
    assert session.state == "hung_up"


def test_a_roguelike_walk_that_reached_no_leaf_reads_as_a_hangup() -> None:
    """"fail" is not a state of its own: the caller's line simply dropped."""
    session = _session()
    session.complete({"mode": "roguelike", "outcome": "fail", "attempts": 0})
    assert session.state == "hung_up"


def test_an_engine_failure_is_not_reported_as_a_caller_hangup() -> None:
    session = _session()
    session.abandon()
    assert session.state == "dropped"
    assert session.is_over
    assert session.ended_at is not None


def test_a_call_abandoned_after_the_caller_left_is_a_hangup() -> None:
    """The handset went down; the exception that followed was its consequence."""
    session = _session()
    session.caller_gone = True
    session.abandon()
    assert session.state == "hung_up"


def test_digits_accumulate_as_the_caller_dials() -> None:
    session = _session()
    for digit in "1234":
        session.record_digit(digit)
    assert session.digits == ["1", "2", "3", "4"]


def test_the_digit_buffer_keeps_only_the_most_recent() -> None:
    """A roguelike walk is unbounded; the display of it is not."""
    session = _session()
    for digit in "1" * (MAX_LIVE_DIGITS + 5):
        session.record_digit(digit)
    session.record_digit("7")
    assert len(session.digits) == MAX_LIVE_DIGITS
    assert session.digits[-1] == "7"


# -- an ending the mode handler never returned (#50) ---------------------------
#
# The mode handler returns exactly once, at a terminal outcome. When the caller
# puts the handset down mid-playback every following ARI command 404s and it
# never returns at all — so the ending has to be synthesised here, or the call
# the Operator just watched leaves no trace in the history.


def test_a_caller_who_hung_up_mid_call_is_persistable_as_a_hangup() -> None:
    session = _session(mode="tweeted")
    session.caller_gone = True
    session.abandon()

    assert session.outcome == "hangup"
    assert session.to_record().outcome == "hangup"


def test_an_engine_failure_persists_as_its_own_outcome() -> None:
    """`dropped` is not a hangup: the caller never chose to end this call."""
    session = _session(mode="tweeted")
    session.abandon()

    assert session.outcome == "dropped"
    assert session.to_record().outcome == "dropped"


def test_an_abandoned_call_claims_only_the_attempts_the_caller_finished() -> None:
    """The same count the tidy path reports: an attempt in flight was not burned."""
    session = _session(mode="tweeted")
    session.begin_attempt(2, 3)
    session.caller_gone = True
    session.abandon()

    assert session.attempts == 1


def test_an_abandoned_walk_counts_the_rooms_it_got_through() -> None:
    session = _session(mode="roguelike")
    session.enter_node(index=7, depth=3, terminal=False)
    session.caller_gone = True
    session.abandon()

    assert session.attempts == 3


def test_an_abandoned_call_that_never_started_claims_nothing() -> None:
    session = _session(mode="puzzle")
    session.abandon()

    assert session.attempts == 0


def test_a_call_abandoned_before_it_had_a_mode_still_refuses_to_be_a_record() -> None:
    """No Config Snapshot, no game, nothing honest to write down."""
    session = _session()
    session.abandon()

    with pytest.raises(ValueError, match="no ending"):
        session.to_record()
