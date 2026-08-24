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
import json
from pathlib import Path
from typing import Any

import pytest

from engine.call_engine import EXILE_MEDIA, WRONG_MEDIA, CallEngine
from engine.call_store import CallStore
from engine.fake_pbx import FakePBX, SilentTTS


def _write_config(config_dir: Path, **config: Any) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "mode.json").write_text(json.dumps(config))


async def _engine(tmp_path: Path, ari: FakePBX, **config: Any) -> tuple[CallEngine, CallStore]:
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
    )
    await engine.start()
    return engine, store


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
    # The call ran to a terminal outcome, not just started.
    assert records[0].outcome in {"succeed", "fail"}


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


def test_second_call_while_busy_is_hung_up(tmp_path: Path) -> None:
    """One booth phone: a StasisStart during a live call is rejected, not queued."""

    async def run() -> Any:
        release = asyncio.Event()

        class GatedPBX(FakePBX):
            async def read_digits(self, channel_id: str, num_digits: int, inter_digit_timeout_ms: int) -> str:
                await release.wait()  # hold the first call open
                return "1234"

        ari = GatedPBX()
        engine, store = await _engine(tmp_path, ari, mode="tweeted", code="1234")

        await ari.fire_stasis_start("chan-1")
        await asyncio.sleep(0.02)  # let the first call reach the gated read
        first_session = engine.active_session

        await ari.fire_stasis_start("chan-2")  # arrives while busy
        second_is_hung_up = ("hangup", "chan-2") in ari.calls
        still_first = engine.active_session is first_session

        release.set()
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
