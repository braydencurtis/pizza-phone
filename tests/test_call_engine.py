"""Tests for the Call Engine skeleton (#19).

The engine is exercised as it runs in production: the Fake PBX stands in for
Asterisk, a real file-backed :class:`CallStore` records the sessions, and each
call is driven by firing a ``StasisStart`` event. The DoD — all three Modes
complete end-to-end — is the first three tests: fire a call, let it run, and
assert the persisted :class:`CallRecord`.

The fake is :class:`engine.fake_pbx.FakePBX`, the same one the development
harness runs (#33): it mirrors how ``ARIClient`` dispatches (handlers run inline
on the reader task) and how ``ARICallIO`` calls it (``read_digits`` scripted per
call), so the sync ``core.flow`` handler runs in a worker thread against the
fake exactly as it will against the real client.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from core.config import write_config
from core.mode_roguelike import REFUSED_KEYS_BEFORE_GONE
from engine.call_engine import EXILE_MEDIA, WRONG_MEDIA, CallEngine
from engine.call_store import CallStore
from engine.fake_pbx import FakePBX, SilentTTS


def _write_config(config_dir: Path, **config: Any) -> None:
    write_config(config_dir / "mode.json", config)


async def _engine(
    tmp_path: Path, ari: FakePBX, *, afterglow_s: float = 0.0, **config: Any
) -> tuple[CallEngine, CallStore]:
    """Wire an engine over ``tmp_path``.

    The afterglow — how long a finished call stays on the Console before the
    booth reads idle — defaults to zero here so ``wait_for_idle()`` means the
    engine is genuinely idle; the tests that care about it set their own.
    """
    config_dir = tmp_path / "config"
    _write_config(config_dir, **config)
    store = CallStore(tmp_path / "calls.db")
    engine = CallEngine(
        ari,  # type: ignore[arg-type]  # structural stand-in for ARIClient
        store,
        config_dir=config_dir,
        log_dir=tmp_path / "logs",
        audio_dir=tmp_path / "audio",
        tts=SilentTTS(),
        afterglow_s=afterglow_s,
    )
    await engine.start()
    return engine, store


class GatedPBX(FakePBX):
    """A fake whose caller holds the line until the test lets go.

    ``read_digits`` blocks on :attr:`release`, so a call can be parked mid-flight
    while the test looks at (or interferes with) the engine's live state.
    """

    def __init__(self, entry: str = "1234") -> None:
        super().__init__()
        self.release = asyncio.Event()
        self._entry = entry

    async def read_digits(
        self, channel_id: str, num_digits: int, inter_digit_timeout_ms: int
    ) -> str:
        await self.release.wait()
        return self._entry


def _seed_puzzle_pool(tmp_path: Path) -> None:
    """Drop one placeholder puzzle WAV where the engine's PuzzleSelector looks."""
    pool = tmp_path / "audio" / "puzzles"
    pool.mkdir(parents=True)
    (pool / "riddle-001.wav").write_bytes(b"RIFF")


# -- DoD: all three modes complete end-to-end on the engine -------------------


def test_tweeted_call_runs_and_persists(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=["1234"])
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)
        await ari.fire_stasis_start("chan-1", number="+15551112222")
        await engine.wait_for_idle()
        return store, ari

    store, ari = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 1
    assert records[0].mode == "tweeted"
    assert records[0].outcome == "succeed"
    assert records[0].caller_id == "+15551112222"
    # The channel was answered and routed onto the success path.
    assert ("answer", "chan-1") in ari.calls
    assert any(c[0] == "continue" for c in ari.calls)


def test_puzzle_call_runs_and_persists(tmp_path: Path) -> None:
    async def run() -> Any:
        _seed_puzzle_pool(tmp_path)
        ari = FakePBX(dtmf=["4242"])
        engine, store = await _engine(tmp_path, ari, mode="puzzle", code="4242", attempt_limit=3)
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return store

    store = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 1
    assert records[0].mode == "puzzle"
    assert records[0].outcome == "succeed"
    assert records[0].detail == {"puzzle_id": "riddle-001.wav"}


def test_roguelike_call_runs_and_persists(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=["1"] * 40)
        engine, store = await _engine(tmp_path, ari, mode="roguelike", code="0000")
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return store

    store = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 1
    assert records[0].mode == "roguelike"
    # The call ran to a terminal outcome, not just started. Which one is not
    # fixed: the tree is regenerated per Call Session, and since #59 a walk that
    # runs its bound out without finding the room is Exiled rather than handed
    # the Code. A caller pressing "1" every time reaches either.
    assert records[0].outcome in {"succeed", "exile"}


# -- parity: the ARI seam reproduces AGI behaviour across every outcome -------
#
# The DoD tests above cover the happy path per mode. These drive the same modes
# through wrong-then-right and all-wrong sequences — the paths the retired AGI
# driver was exercised on (tests/test_flow.py) — but end-to-end through the
# engine and ARICallIO, so deleting agi/ can't silently drop coverage of the
# wrong-answer beep, the Exile prompt, or the attempt limit on the ARI path.


def test_tweeted_wrong_then_right_succeeds(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=["0000", "1234"])
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return store, ari

    store, ari = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 1
    assert records[0].outcome == "succeed"
    assert records[0].attempts == 2
    # One wrong answer beeps once; success routes onto the dialplan, no hangup.
    assert ari.calls.count(("play", "chan-1", WRONG_MEDIA)) == 1
    assert any(c[0] == "continue" for c in ari.calls)
    assert ("hangup", "chan-1") not in ari.calls


def test_tweeted_all_wrong_exiles(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=["0000", "0000", "0000"])
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return store, ari

    store, ari = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 1
    assert records[0].outcome == "exile"
    assert records[0].attempts == 3
    # Two beeps (attempts 1-2), then the Exile prompt, then hang up.
    assert ari.calls.count(("play", "chan-1", WRONG_MEDIA)) == 2
    assert ("play", "chan-1", EXILE_MEDIA) in ari.calls
    assert ("hangup", "chan-1") in ari.calls
    assert not any(c[0] == "continue" for c in ari.calls)


def test_puzzle_wrong_then_right_succeeds(tmp_path: Path) -> None:
    async def run() -> Any:
        _seed_puzzle_pool(tmp_path)
        ari = FakePBX(dtmf=["0000", "4242"])
        engine, store = await _engine(tmp_path, ari, mode="puzzle", code="4242", attempt_limit=3)
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return store, ari

    store, ari = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 1
    assert records[0].outcome == "succeed"
    assert records[0].attempts == 2
    assert records[0].detail == {"puzzle_id": "riddle-001.wav"}
    # The riddle prompt plays before answers; one wrong answer beeps once.
    assert ari.calls.count(("play", "chan-1", WRONG_MEDIA)) == 1
    assert any(c[0] == "continue" for c in ari.calls)


def test_puzzle_all_wrong_exiles(tmp_path: Path) -> None:
    async def run() -> Any:
        _seed_puzzle_pool(tmp_path)
        ari = FakePBX(dtmf=["0000", "1111", "2222"])
        engine, store = await _engine(tmp_path, ari, mode="puzzle", code="4242", attempt_limit=3)
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return store, ari

    store, ari = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 1
    assert records[0].outcome == "exile"
    assert records[0].attempts == 3
    assert ari.calls.count(("play", "chan-1", WRONG_MEDIA)) == 2
    assert ("play", "chan-1", EXILE_MEDIA) in ari.calls
    assert ("hangup", "chan-1") in ari.calls


# -- skeleton behaviours ------------------------------------------------------


def test_hangup_without_input_is_persisted(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=[])  # caller picks up, enters nothing
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234")
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return store

    store = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 1
    assert records[0].outcome == "hangup"


def test_a_silent_roguelike_caller_frees_the_booth_for_the_next_one(tmp_path: Path) -> None:
    """The bricked booth (#53): one handset off the hook used to close it for good.

    The maze re-asked a silent caller forever, and the engine holds one call at
    a time — so every caller behind them was hung up on until somebody walked
    over and replaced the handset. The second caller here is the whole point:
    they must be taken, not refused as busy.
    """

    async def run() -> Any:
        ari = FakePBX(dtmf=[])  # picks up, presses nothing, never puts it down
        engine, store = await _engine(tmp_path, ari, mode="roguelike", code="0000")
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()

        ari.script(["1"] * 40)  # the next caller actually plays
        await ari.fire_stasis_start("chan-2")
        await engine.wait_for_idle()
        return engine, store, ari

    engine, store, ari = asyncio.run(run())
    assert engine.active_session is None
    # The silent call was torn down, and the next caller was never refused.
    assert ("hangup", "chan-1") in ari.calls
    assert ("answer", "chan-2") in ari.calls

    records = asyncio.run(store.query())
    assert len(records) == 2, "both callers were taken"
    # A refused call is hung up without ever being answered and is never
    # persisted, so two records with the second answered is the proof. Its
    # outcome is not asserted: since #59 a maze walk can end either way, and
    # what this test is about is that the caller got to walk at all.
    silent = asyncio.run(store.query(outcome="hangup"))
    assert len(silent) == 1
    assert silent[0].mode == "roguelike"


class WedgedKeyPBX(FakePBX):
    """A handset with a key stuck down: every read hands back the same digit.

    Not an exhausted script — that is silence, and silence has been an ending
    since #53. This caller presses a real key, forever, and the maze offers only
    "1" and "2", so before #55 the room replayed for as long as the handset lay
    there. The patience bound makes that fail the suite rather than hang it.
    """

    PATIENCE = 40

    def __init__(self, digit: str = "9") -> None:
        super().__init__()
        self._digit = digit
        # Public: clearing it is somebody putting the handset back, which is how
        # the second caller in the test below gets a working phone.
        self.wedged = True
        self.reads = 0

    async def read_digits(
        self, channel_id: str, num_digits: int, inter_digit_timeout_ms: int
    ) -> str:
        if not self.wedged:
            return await super().read_digits(channel_id, num_digits, inter_digit_timeout_ms)
        self.reads += 1
        if self.reads > self.PATIENCE:
            raise AssertionError(
                f"a wedged key was read {self.PATIENCE} times — the engine is looping"
            )
        await self.fire_dtmf(channel_id, self._digit)
        return self._digit


def test_a_wedged_key_in_the_maze_frees_the_booth_for_the_next_caller(tmp_path: Path) -> None:
    """The other half of the bricked booth (#55).

    #53 stopped a silent caller holding the slot; a caller whose key is wedged on
    a digit the room does not offer walked the same infinite loop, because a
    refused key makes no move and ``max_depth`` bounds moves. Same test shape,
    same thing at stake: the second caller must be taken.
    """

    async def run() -> Any:
        ari = WedgedKeyPBX()
        engine, store = await _engine(tmp_path, ari, mode="roguelike", code="0000")
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()

        wedged_reads = ari.reads
        ari.wedged = False  # the handset is put back on its cradle
        ari.script(["1"] * 40)  # the next caller actually plays
        await ari.fire_stasis_start("chan-2")
        await engine.wait_for_idle()
        return engine, store, ari, wedged_reads

    engine, store, ari, wedged_reads = asyncio.run(run())
    assert engine.active_session is None
    # The room was replayed to the bound — the fumble is still forgiven — and
    # then the call was torn down rather than asked again forever.
    assert wedged_reads == REFUSED_KEYS_BEFORE_GONE
    assert ("hangup", "chan-1") in ari.calls
    assert ("answer", "chan-2") in ari.calls

    records = asyncio.run(store.query())
    assert len(records) == 2, "both callers were taken"
    wedged = asyncio.run(store.query(outcome="hangup"))
    assert len(wedged) == 1
    assert wedged[0].mode == "roguelike"


def test_second_call_while_busy_is_hung_up(tmp_path: Path) -> None:
    """One booth phone: a StasisStart during a live call is rejected, not queued."""

    async def run() -> Any:
        ari = GatedPBX()  # holds the first call open
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234")

        await ari.fire_stasis_start("chan-1")
        await asyncio.sleep(0.02)  # let the first call reach the gated read
        first_session = engine.active_session

        await ari.fire_stasis_start("chan-2")  # arrives while busy
        second_is_hung_up = ("hangup", "chan-2") in ari.calls
        still_first = engine.active_session is first_session

        ari.release.set()
        await engine.wait_for_idle()
        return store, second_is_hung_up, still_first

    store, second_is_hung_up, still_first = asyncio.run(run())
    assert second_is_hung_up
    assert still_first  # the busy channel never displaced the live session
    records = asyncio.run(store.query())
    assert len(records) == 1  # only the first call was recorded


def test_unknown_mode_hangs_up_and_frees_the_slot(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX()
        engine, store = await _engine(tmp_path, ari, mode="bogus", code="1234")
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return engine, store, ari

    engine, store, ari = asyncio.run(run())
    assert engine.active_session is None  # slot freed despite the failure
    assert ("hangup", "chan-1") in ari.calls
    assert asyncio.run(store.query()) == []  # nothing persisted for a failed call


def test_aclose_drains_the_active_call_then_closes(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=["1234"])
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234")
        await ari.fire_stasis_start("chan-1")
        await engine.aclose()
        return store, ari

    store, ari = asyncio.run(run())
    # The call finished (recorded) before the connection closed.
    assert len(asyncio.run(store.query())) == 1
    assert ari.calls[-1] == ("close",)


def test_start_connects_and_registers(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX()
        await _engine(tmp_path, ari, mode="tweeted", code="1234")
        return ari

    ari = asyncio.run(run())
    assert ("connect",) in ari.calls


# -- Config Snapshot at pickup (#34) ------------------------------------------


def test_the_live_session_carries_the_config_it_picked_up_with(tmp_path: Path) -> None:
    """The console reads the snapshot off the session to show the game in play."""

    async def run() -> Any:
        seen: list[Any] = []

        class WatchingPBX(GatedPBX):
            async def read_digits(
                self, channel_id: str, num_digits: int, inter_digit_timeout_ms: int
            ) -> str:
                session = engine.active_session
                seen.append(session.config if session else None)
                return await super().read_digits(channel_id, num_digits, inter_digit_timeout_ms)

        ari = WatchingPBX()
        engine, _store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)
        await ari.fire_stasis_start("chan-1")
        await asyncio.sleep(0.02)
        ari.release.set()
        await engine.wait_for_idle()
        return seen

    seen = asyncio.run(run())
    assert seen and seen[0] is not None
    assert (seen[0].mode, seen[0].code, seen[0].attempt_limit) == ("tweeted", "1234", 3)


def test_a_code_rotated_mid_call_does_not_change_the_live_call(tmp_path: Path) -> None:
    """The booth bug: the caller answers the riddle they were played and wins."""

    async def run() -> Any:
        _seed_puzzle_pool(tmp_path)

        class RotatingPBX(FakePBX):
            async def read_digits(
                self, channel_id: str, num_digits: int, inter_digit_timeout_ms: int
            ) -> str:
                # The Operator rotates the Code between the caller's attempts.
                _write_config(tmp_path / "config", mode="tweeted", code="8080", attempt_limit=3)
                return await super().read_digits(channel_id, num_digits, inter_digit_timeout_ms)

        ari = RotatingPBX(dtmf=["0000", "4242"])
        engine, store = await _engine(tmp_path, ari, mode="puzzle", code="4242", attempt_limit=3)
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return store

    store = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 1
    assert records[0].mode == "puzzle"  # not the mode the Operator switched to
    assert records[0].outcome == "succeed"


def test_a_config_change_during_a_call_applies_to_the_next_call(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=["1234"])
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()

        _write_config(tmp_path / "config", mode="tweeted", code="8080", attempt_limit=3)
        ari.script(["8080"])
        await ari.fire_stasis_start("chan-2")
        await engine.wait_for_idle()
        return store

    store = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 2
    assert {r.outcome for r in records} == {"succeed"}


# -- the console's change hook (#35) ------------------------------------------


def test_the_engine_announces_a_call_starting_and_ending(tmp_path: Path) -> None:
    """The seam the Console pushes snapshots from: state moved, come and look."""

    async def run() -> Any:
        ari = FakePBX(dtmf=["1234"])
        engine, _store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)

        # Record what the live state *was* at each announcement, since that is
        # all the Console does with it: build a snapshot and broadcast.
        seen: list[str | None] = []
        engine.on_change(
            lambda: seen.append(
                engine.active_session.session_id if engine.active_session else None
            )
        )

        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return seen

    seen = asyncio.run(run())
    assert seen, "a call came and went without the console hearing about it"
    assert seen[0] is not None, "the first announcement should carry the new call"
    assert seen[-1] is None, "the last announcement should be the booth going idle"


def test_a_console_can_stop_listening(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=["1234"])
        engine, _store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)
        calls: list[int] = []
        unsubscribe = engine.on_change(lambda: calls.append(1))
        unsubscribe()
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return calls

    assert asyncio.run(run()) == []


def test_a_broken_console_subscriber_does_not_break_the_call(tmp_path: Path) -> None:
    """A dead browser socket must not cost the caller their call."""

    async def run() -> Any:
        ari = FakePBX(dtmf=["1234"])
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)

        def explode() -> None:
            raise RuntimeError("console blew up")

        engine.on_change(explode)
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return store

    store = asyncio.run(run())
    records = asyncio.run(store.query())
    assert len(records) == 1
    assert records[0].outcome == "succeed"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


# -- the live state vocabulary and digits (#36) -------------------------------


def _states(engine: CallEngine, seen: list[Any]) -> Callable[[], None]:
    """Record the live state at each announcement — what the Console renders."""

    def note() -> None:
        session = engine.active_session
        seen.append(None if session is None else session.state)

    return note


def test_a_call_walks_the_state_vocabulary_to_a_win(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=["1234"])
        engine, _store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)
        seen: list[Any] = []
        engine.on_change(_states(engine, seen))
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return seen

    seen = asyncio.run(run())
    assert seen[0] == "answering"
    assert "in_mode" in seen
    assert seen[-2] == "handed_off"  # the win is announced before the booth idles
    assert seen[-1] is None


def test_an_exile_and_a_hangup_are_announced_as_themselves(tmp_path: Path) -> None:
    async def run(dtmf: list[str]) -> Any:
        ari = FakePBX(dtmf=dtmf)
        engine, _store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)
        seen: list[Any] = []
        engine.on_change(_states(engine, seen))
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return seen

    assert asyncio.run(run(["9999", "8888", "7777"]))[-2] == "exiled"
    assert asyncio.run(run([]))[-2] == "hung_up"


def test_a_failed_call_is_dropped_not_reported_as_a_hangup(tmp_path: Path) -> None:
    """An engine failure must not masquerade as the caller walking away."""

    async def run() -> Any:
        ari = FakePBX()
        engine, _store = await _engine(tmp_path, ari, mode="bogus", code="1234")
        seen: list[Any] = []
        engine.on_change(_states(engine, seen))
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return seen

    assert asyncio.run(run())[-2] == "dropped"


def test_dialled_digits_reach_the_live_session_as_they_arrive(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=["1234"])
        engine, _store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)
        seen: list[str] = []
        engine.on_change(
            lambda: seen.append(
                "".join(engine.active_session.digits) if engine.active_session else ""
            )
        )
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return seen

    seen = asyncio.run(run())
    # Every prefix of the dialled code was visible in turn, not just the whole.
    assert "1" in seen and "12" in seen and "123" in seen and "1234" in seen


def test_digits_from_another_channel_are_ignored(tmp_path: Path) -> None:
    """Only the call the booth is on may write to the console's digit display."""

    async def run() -> Any:
        ari = GatedPBX()
        engine, _store = await _engine(tmp_path, ari, mode="tweeted", code="1234")
        await ari.fire_stasis_start("chan-1")
        await asyncio.sleep(0.02)
        await ari.fire_dtmf("chan-2", "9")
        digits = list(engine.active_session.digits) if engine.active_session else None
        ari.release.set()
        await engine.wait_for_idle()
        return digits

    assert asyncio.run(run()) == []


# -- the afterglow: a finished call stays readable, then the booth idles -------


def test_a_finished_call_is_held_in_view_before_the_booth_goes_idle(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = FakePBX(dtmf=["1234"])
        engine, _store = await _engine(
            tmp_path, ari, afterglow_s=0.15, mode="tweeted", code="1234", attempt_limit=3
        )
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        held = engine.active_session
        await asyncio.sleep(0.3)
        return held, engine.active_session

    held, after = asyncio.run(run())
    assert held is not None and held.state == "handed_off"
    assert after is None


def test_a_new_call_during_the_afterglow_is_taken_not_refused(tmp_path: Path) -> None:
    """The booth is free the moment the call ends — the afterglow is display only."""

    async def run() -> Any:
        ari = FakePBX(dtmf=["1234", "1234"])
        engine, store = await _engine(
            tmp_path, ari, afterglow_s=5.0, mode="tweeted", code="1234", attempt_limit=3
        )
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        await ari.fire_stasis_start("chan-2")
        await engine.wait_for_idle()
        await engine.aclose()
        return store, ari

    store, ari = asyncio.run(run())
    assert ("hangup", "chan-2") not in ari.calls  # never refused as busy
    assert len(asyncio.run(store.query())) == 2


def test_a_caller_who_hangs_up_mid_call_is_not_blamed_on_the_engine(tmp_path: Path) -> None:
    """A dead channel makes the next ARI call fail; that is not an engine fault.

    The handset going down mid-playback is the commonest ending there is, and
    reporting it as "the engine dropped the call" would be the panel lying about
    who ended it — the exact failure the state vocabulary exists to prevent.
    """

    async def run() -> Any:
        class VanishingPBX(FakePBX):
            async def play(
                self, channel_id: str, media: str, *, timeout: float | None = None
            ) -> None:
                await self.fire_channel_gone(channel_id)
                raise RuntimeError("channel is gone (404)")

        ari = VanishingPBX()
        engine, _store = await _engine(tmp_path, ari, mode="puzzle", code="1234")
        _seed_puzzle_pool(tmp_path)
        seen: list[Any] = []
        engine.on_change(_states(engine, seen))
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return seen

    assert asyncio.run(run())[-2] == "hung_up"


def test_an_engine_failure_with_the_caller_still_there_is_dropped(tmp_path: Path) -> None:
    """The other half of the same fork: nobody hung up, so it was us."""

    async def run() -> Any:
        ari = FakePBX()
        engine, _store = await _engine(tmp_path, ari, mode="bogus", code="1234")
        seen: list[Any] = []
        engine.on_change(_states(engine, seen))
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return seen

    assert asyncio.run(run())[-2] == "dropped"


def test_a_hangup_on_another_channel_does_not_touch_the_live_call(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = GatedPBX()
        engine, _store = await _engine(tmp_path, ari, mode="tweeted", code="1234")
        await ari.fire_stasis_start("chan-1")
        await asyncio.sleep(0.02)
        await ari.fire_channel_gone("chan-2")
        gone = engine.active_session.caller_gone if engine.active_session else None
        ari.release.set()
        await engine.wait_for_idle()
        return gone

    assert asyncio.run(run()) is False


# -- live progress off the CallObserver seam, through the real thread hop (#37)
#
# The unit tests in tests/test_call_observer.py drive the observer against a
# hand-made loop. These drive the wiring: a real call, in a real worker thread,
# emitting through the real `EngineCallObserver` into the session the Console
# reads. `GatedPBX` parks the caller mid-attempt so the state can be read while
# the call is genuinely in flight rather than after it.


def test_a_live_call_reports_the_attempt_it_is_on(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = GatedPBX(entry="1234")
        engine, _store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)
        await ari.fire_stasis_start("chan-1")
        # Parked inside read_digits, so the flow has announced attempt 1 and is
        # waiting on the caller — exactly what the Operator would be watching.
        await _settle()

        session = engine.active_session
        assert session is not None
        assert session.current_attempt == 1
        assert session.attempt_limit == 3
        assert session.attempts == 0, "the final count is not known until the handler returns"

        ari.release.set()
        await engine.wait_for_idle()
        return engine

    engine = asyncio.run(run())
    assert engine is not None


def test_a_live_roguelike_call_reports_where_in_the_maze_the_caller_is(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = GatedPBX(entry="1")
        engine, _store = await _engine(tmp_path, ari, mode="roguelike", code="0000")
        await ari.fire_stasis_start("chan-1")
        await _settle()

        session = engine.active_session
        assert session is not None
        assert session.node is not None
        assert (session.node.index, session.node.depth) == (0, 0), "parked at the mouth"

        ari.release.set()
        await engine.wait_for_idle()
        return engine.active_session

    asyncio.run(run())


def test_a_live_puzzle_call_reports_which_riddle_was_drawn(tmp_path: Path) -> None:
    async def run() -> Any:
        _seed_puzzle_pool(tmp_path)
        ari = GatedPBX(entry="4242")
        engine, _store = await _engine(tmp_path, ari, mode="puzzle", code="4242", attempt_limit=3)
        await ari.fire_stasis_start("chan-1")
        await _settle()

        session = engine.active_session
        assert session is not None
        assert session.puzzle_id == "riddle-001.wav"

        ari.release.set()
        await engine.wait_for_idle()
        return engine.active_session

    asyncio.run(run())


def test_live_progress_reaches_the_console_as_it_happens(tmp_path: Path) -> None:
    """The whole point: the change hook fires, so a browser is told mid-call.

    Without this the fields would be correct in memory and invisible on screen —
    the snapshot is only built when something announces that state moved.
    """

    async def run() -> Any:
        ari = GatedPBX(entry="1234")
        engine, _store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)

        seen: list[int | None] = []
        engine.on_change(lambda: seen.append(_attempt_of(engine)))

        await ari.fire_stasis_start("chan-1")
        await _settle()
        ari.release.set()
        await engine.wait_for_idle()
        return seen

    seen = asyncio.run(run())
    assert 1 in seen, f"the Console was never told which attempt the caller was on: {seen}"


def _attempt_of(engine: CallEngine) -> int | None:
    session = engine.active_session
    return None if session is None else session.current_attempt


async def _settle() -> None:
    """Let the call task reach the parked read, and its emissions reach the loop.

    The observer hops to the loop with `call_soon_threadsafe`, so the write lands
    a tick after the worker thread makes it; a bare `sleep(0)` can miss it.
    """
    for _ in range(20):
        await asyncio.sleep(0.01)


# -- the history agrees with the panel (#50) ----------------------------------
#
# A call that ends on an exception used to be announced to the Console and then
# forgotten: the cockpit said "hung up" while the store held no row at all, so
# the two disagreed about a call the Operator had just watched. These drive the
# whole path — a real call, a real terminal state, a real `CallStore`.


class VanishingPBX(FakePBX):
    """A caller who puts the handset down mid-playback.

    The channel is gone before `play` returns, so this and every later ARI
    command 404s — which is exactly how the commonest ending there is reaches
    the engine: as an exception out of the mode handler.
    """

    async def play(self, channel_id: str, media: str, *, timeout: float | None = None) -> None:
        await self.fire_channel_gone(channel_id)
        raise RuntimeError("channel is gone (404)")


def test_a_caller_who_hangs_up_mid_call_leaves_a_record(tmp_path: Path) -> None:
    async def run() -> Any:
        ari = VanishingPBX()
        engine, store = await _engine(tmp_path, ari, mode="puzzle", code="1234")
        _seed_puzzle_pool(tmp_path)
        seen: list[Any] = []
        engine.on_change(_states(engine, seen))
        await ari.fire_stasis_start("chan-1")
        live_id = engine.active_session.session_id if engine.active_session else None
        await engine.wait_for_idle()
        return seen, live_id, await store.query()

    seen, live_id, records = asyncio.run(run())
    # The panel said "hung up"; the history must say the same about that call.
    assert seen[-2] == "hung_up"
    assert len(records) == 1
    assert records[0].session_id == live_id
    assert records[0].outcome == "hangup"
    assert records[0].mode == "puzzle"


def test_an_engine_failure_does_not_inflate_the_hangup_count(tmp_path: Path) -> None:
    """A call the engine broke is not one the caller walked away from."""

    class BrokenPBX(FakePBX):
        """Playback fails with the caller still on the line — our fault, not theirs."""

        async def play(self, channel_id: str, media: str, *, timeout: float | None = None) -> None:
            raise RuntimeError("playback exploded")

    async def run() -> Any:
        ari = BrokenPBX()
        engine, store = await _engine(tmp_path, ari, mode="puzzle", code="1234")
        _seed_puzzle_pool(tmp_path)
        seen: list[Any] = []
        engine.on_change(_states(engine, seen))
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return seen, await store.query(), await store.query(outcome="hangup")

    seen, records, hangups = asyncio.run(run())
    # The panel blamed the engine; so does the row, rather than the caller.
    assert seen[-2] == "dropped"
    assert [record.outcome for record in records] == ["dropped"]
    assert hangups == []


def test_a_store_failure_does_not_cost_the_console_its_terminal_state(tmp_path: Path) -> None:
    """The call is over and the channel is down — a lost row is not worth more.

    Persisting moved into the `finally` alongside the announcement, so a store
    that throws sits between the caller's ending and the Operator seeing it.
    """

    async def run() -> Any:
        ari = FakePBX(dtmf=["1234"])
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234", attempt_limit=3)

        async def explode(record: Any) -> None:
            raise RuntimeError("the disk is on fire")

        store.add = explode  # type: ignore[method-assign]
        seen: list[Any] = []
        engine.on_change(_states(engine, seen))
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return seen

    seen = asyncio.run(run())
    # The win still reached the panel, and the call task did not blow up.
    assert seen[-2] == "handed_off"
    assert seen[-1] is None


def test_an_abandoned_puzzle_call_remembers_which_riddle_it_was(tmp_path: Path) -> None:
    """What the engine has in hand off the CallObserver seam, it writes down."""

    async def run() -> Any:
        ari = VanishingPBX()
        engine, store = await _engine(tmp_path, ari, mode="puzzle", code="1234")
        _seed_puzzle_pool(tmp_path)
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return await store.query()

    records = asyncio.run(run())
    assert records[0].detail == {"puzzle_id": "riddle-001.wav"}


def test_a_call_that_never_got_a_config_snapshot_persists_nothing(tmp_path: Path) -> None:
    """No Mode, no game — there is nothing to write down but the engine log."""

    async def run() -> Any:
        ari = FakePBX()
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234")
        (tmp_path / "config" / "mode.json").unlink()
        seen: list[Any] = []
        engine.on_change(_states(engine, seen))
        await ari.fire_stasis_start("chan-1")
        await engine.wait_for_idle()
        return seen, await store.query()

    seen, records = asyncio.run(run())
    assert seen[-2] == "dropped"
    assert records == []
