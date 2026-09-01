"""The snapshot stream a Call Session produces, end to end (#36).

The other suites test the pieces: :mod:`tests.test_snapshot` the wire shape,
:mod:`tests.test_call_engine` the engine's state transitions,
:mod:`tests.test_console_server` the socket. This one asserts the thing the
Operator actually gets — the *sequence* of whole-state snapshots a call
produces, from the booth idling through the caller dialling to the booth idling
again, once per Mode.

It is assembled exactly as the Console server assembles it: subscribe to
``CallEngine.on_change``, read Global Config, and build a snapshot from the live
session. Everything below Asterisk is real — the real ``core.flow`` handlers,
the real ``ARICallIO`` seam, the real store — with the Fake PBX standing in for
the PBX and dialling the scripted digits one key at a time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from core.config import take_snapshot, write_config
from engine.call_engine import CallEngine
from engine.call_store import CallStore
from engine.fake_pbx import FakePBX, SilentTTS
from engine.snapshot import SNAPSHOT_SCHEMA_VERSION, build_snapshot

CALLER_ID = "+15550001234"


async def _run_call(
    tmp_path: Path, dtmf: list[str], *, afterglow_s: float = 0.0, **config: Any
) -> list[dict[str, Any]]:
    """Place one synthetic call and return every snapshot the Console would see.

    The first entry is the idle booth before the phone rings, so the sequence
    each test asserts is the whole arc rather than its middle.
    """
    config_dir = tmp_path / "config"
    config_path = config_dir / "mode.json"
    write_config(config_path, config)
    _seed_puzzle_pool(tmp_path)

    store = CallStore(tmp_path / "calls.db")
    ari = FakePBX(dtmf=dtmf)
    engine = CallEngine(
        ari,  # type: ignore[arg-type]  # structural stand-in for ARIClient
        store,
        config_dir=config_dir,
        log_dir=tmp_path / "logs",
        audio_dir=tmp_path / "audio",
        tts=SilentTTS(),
        # Zero by default: the terminal snapshot and the idle one that follows
        # are both recorded here without a test waiting out a display delay.
        afterglow_s=afterglow_s,
    )
    await engine.start()

    snapshots: list[dict[str, Any]] = []

    def capture() -> None:
        snapshots.append(build_snapshot(take_snapshot(config_path), engine.active_session))

    capture()  # the idle booth, before the phone rings
    engine.on_change(capture)
    await ari.fire_stasis_start("chan-1", number=CALLER_ID)
    await engine.wait_for_idle()
    if afterglow_s:
        await asyncio.sleep(afterglow_s * 2)  # let the booth go idle again
    await engine.aclose()
    return snapshots


def _seed_puzzle_pool(tmp_path: Path) -> None:
    pool = tmp_path / "audio" / "puzzles"
    pool.mkdir(parents=True, exist_ok=True)
    (pool / "riddle-001.wav").write_bytes(b"RIFF")


def _states(snapshots: list[dict[str, Any]]) -> list[str | None]:
    """The state of each snapshot in turn, ``None`` for an idle booth."""
    return [None if s["call"] is None else s["call"]["state"] for s in snapshots]


def _arc(snapshots: list[dict[str, Any]]) -> list[str | None]:
    """The states the call passed through, in order, without the repeats.

    A snapshot is sent per change, and most changes are a digit rather than a
    move to a new state — so collapsing runs leaves exactly the arc the Operator
    watched, which is what each Mode's test asserts whole.
    """
    states = _states(snapshots)
    return [state for i, state in enumerate(states) if i == 0 or state != states[i - 1]]


def _digit_trail(snapshots: list[dict[str, Any]]) -> list[str]:
    return [s["call"]["digits"] for s in snapshots if s["call"] is not None]


# -- the arc, once per Mode ---------------------------------------------------


def test_a_tweeted_win_is_answered_played_and_handed_off(tmp_path: Path) -> None:
    snapshots = asyncio.run(
        _run_call(tmp_path, ["1234"], mode="tweeted", code="1234", attempt_limit=3)
    )
    # Idle booth → the call arriving → the game → the win → idle again.
    assert _arc(snapshots) == [None, "answering", "in_mode", "handed_off", None]

    # Every snapshot is the whole truth, at the schema this console speaks.
    assert {s["schema"] for s in snapshots} == {SNAPSHOT_SCHEMA_VERSION}
    assert all(s["config"]["code"] == "1234" for s in snapshots)

    call = snapshots[-2]["call"]
    assert call["caller_id"] == CALLER_ID
    assert call["mode"] == "tweeted"
    assert call["outcome"] == "succeed"
    assert call["digits"] == "1234"
    # The clock is the browser's to advance: a start time, and an end once the
    # call is over — never a duration counted on the engine.
    assert call["started_at"] and call["ended_at"]
    assert "duration" not in call


def test_the_digits_arrive_one_key_at_a_time(tmp_path: Path) -> None:
    snapshots = asyncio.run(
        _run_call(tmp_path, ["1234"], mode="tweeted", code="1234", attempt_limit=3)
    )
    trail = _digit_trail(snapshots)
    # Consecutive duplicates are the snapshots a digit didn't cause; what is
    # left is the display the Operator watched fill up.
    typed = [d for i, d in enumerate(trail) if i == 0 or d != trail[i - 1]]
    assert typed == ["", "1", "12", "123", "1234"]


def test_a_puzzle_caller_who_burns_the_attempt_limit_is_exiled(tmp_path: Path) -> None:
    snapshots = asyncio.run(
        _run_call(
            tmp_path,
            ["1111", "2222", "3333"],
            mode="puzzle",
            code="4242",
            attempt_limit=3,
        )
    )
    assert _arc(snapshots) == [None, "answering", "in_mode", "exiled", None]

    call = snapshots[-2]["call"]
    assert call["mode"] == "puzzle"
    assert call["outcome"] == "exile"
    assert call["attempts"] == 3
    # Wrong answers are still shown: the Operator watches the caller miss.
    assert call["digits"].endswith("3333")


def test_a_caller_who_dials_nothing_hangs_up(tmp_path: Path) -> None:
    snapshots = asyncio.run(_run_call(tmp_path, [], mode="tweeted", code="1234", attempt_limit=3))
    assert _arc(snapshots) == [None, "answering", "in_mode", "hung_up", None]

    call = snapshots[-2]["call"]
    assert call["digits"] == "", "nobody dialled anything"
    assert call["outcome"] == "hangup"


def test_a_roguelike_walk_reports_its_mode_and_a_terminal_state(tmp_path: Path) -> None:
    snapshots = asyncio.run(
        _run_call(tmp_path, ["1"] * 40, mode="roguelike", code="8675", attempt_limit=3)
    )
    arc = _arc(snapshots)

    # The maze walks a freshly generated tree, so where it ends is not fixed:
    # handed off at the leaf, or the line going dead — but never Exiled, since
    # roguelike has no attempt limit and no code entry.
    assert arc[:3] == [None, "answering", "in_mode"]
    assert arc[3] in {"handed_off", "hung_up"}
    assert arc[4:] == [None]

    call = snapshots[-2]["call"]
    assert call["mode"] == "roguelike"
    assert call["digits"], "the caller's key presses were visible as they walked"


def test_the_mode_is_on_the_snapshot_before_the_caller_does_anything(tmp_path: Path) -> None:
    """It is the first thing the Operator wants to know about a new call."""
    snapshots = asyncio.run(
        _run_call(tmp_path, ["1234"], mode="tweeted", code="1234", attempt_limit=3)
    )
    answering = [s["call"] for s in snapshots if s["call"] and s["call"]["state"] == "answering"]
    assert answering[-1]["mode"] == "tweeted"
    assert answering[-1]["digits"] == ""
    assert answering[-1]["ended_at"] is None


def test_a_win_and_a_hangup_never_look_alike(tmp_path: Path) -> None:
    """The distinction the Console exists to make, asserted on the wire."""
    won = asyncio.run(_run_call(tmp_path / "won", ["1234"], mode="tweeted", code="1234"))
    quit_ = asyncio.run(_run_call(tmp_path / "quit", [], mode="tweeted", code="1234"))
    assert won[-2]["call"]["state"] != quit_[-2]["call"]["state"]
    assert won[-2]["call"]["state"] == "handed_off"


def test_the_arc_is_the_same_when_the_finished_call_lingers(tmp_path: Path) -> None:
    """The afterglow delays the last step of the arc; it does not change it."""
    snapshots = asyncio.run(
        _run_call(
            tmp_path,
            ["1234"],
            afterglow_s=0.05,
            mode="tweeted",
            code="1234",
            attempt_limit=3,
        )
    )
    assert _arc(snapshots) == [None, "answering", "in_mode", "handed_off", None]
