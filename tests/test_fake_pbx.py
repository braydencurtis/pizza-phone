"""Tests for the Fake PBX development harness (#33).

The harness is a dev tool, so these tests assert the two things a dev tool has
to get right: the synthetic calls it fires are *real* calls as far as everything
below Asterisk is concerned (they run the actual mode handlers through the
actual ``CallIO`` seam and land in the actual store), and the synthetic audio it
emits is real audio a Listen-in consumer could play.

Everything here runs at ``INSTANT`` pacing with a zero interval; the lifelike
pacing the harness ships with exists for watching the Console, not for tests.
"""

from __future__ import annotations

import asyncio
import json
import struct
import wave
from pathlib import Path
from typing import Any

import pytest

from engine import fake_audio, fake_pbx
from engine.fake_audio import (
    FRAME_BYTES,
    SAMPLE_RATE_HZ,
    SAMPLES_PER_FRAME,
    SyntheticAudioStream,
    WavTee,
    slin_frame,
)
from engine.fake_pbx import (
    DEFAULT_SCENARIOS,
    INSTANT,
    FakePBX,
    Scenario,
    build_fake_harness,
    prepare_workspace,
)


def _samples(frame: bytes) -> tuple[int, ...]:
    """Decode a slin16 frame back into signed 16-bit samples."""
    return struct.unpack(f"<{len(frame) // 2}h", frame)


# -- synthetic audio: the frames a Listen-in consumer would receive -----------


def test_frame_is_one_20ms_slin16_frame() -> None:
    frame = slin_frame(0)
    assert len(frame) == FRAME_BYTES
    # 8 kHz, 16-bit, mono: 20 ms is 160 samples / 320 bytes.
    assert FRAME_BYTES == SAMPLES_PER_FRAME * 2
    assert SAMPLES_PER_FRAME == SAMPLE_RATE_HZ * fake_audio.FRAME_MS // 1000


def test_frames_carry_audible_signal_within_16bit_range() -> None:
    frame = slin_frame(0)
    samples = _samples(frame)
    assert any(s != 0 for s in samples)  # not silence
    assert all(-32768 <= s <= 32767 for s in samples)


def test_successive_frames_advance_through_the_tone() -> None:
    """Frames are a continuing waveform, not the same buffer repeated."""
    first = slin_frame(0)
    second = slin_frame(SAMPLES_PER_FRAME)
    assert first != second


def test_tone_frequency_changes_the_waveform() -> None:
    assert slin_frame(0, frequency_hz=440.0) != slin_frame(0, frequency_hz=523.0)


def test_audio_stream_delivers_paced_frames_until_stopped() -> None:
    async def run() -> tuple[list[bytes], int]:
        frames: list[bytes] = []
        stream = SyntheticAudioStream(frames.append)
        stream.start()
        await asyncio.sleep(0.07)  # a few 20 ms frames
        await stream.stop()
        settled = len(frames)
        await asyncio.sleep(0.06)  # nothing more arrives once stopped
        return frames, settled

    frames, settled = asyncio.run(run())
    assert settled >= 2
    assert len(frames) == settled
    assert all(len(f) == FRAME_BYTES for f in frames)


def test_wav_tee_writes_a_playable_wav(tmp_path: Path) -> None:
    path = tmp_path / "listen-in.wav"
    tee = WavTee(path)
    try:
        for i in range(5):
            tee(slin_frame(i * SAMPLES_PER_FRAME))
    finally:
        tee.close()

    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == SAMPLE_RATE_HZ
        assert wav.getnframes() == 5 * SAMPLES_PER_FRAME


# -- the workspace: a dev sandbox, never the live config or call store --------


def test_prepare_workspace_seeds_config_and_a_puzzle_pool(tmp_path: Path) -> None:
    workspace = prepare_workspace(tmp_path / "fake")

    config = json.loads(workspace.config_path.read_text())
    assert config["mode"] in {"tweeted", "puzzle", "roguelike"}
    # Puzzle mode needs a pool to pick from, so the harness brings its own.
    assert list((workspace.audio_dir / "puzzles").glob("*.wav"))
    assert workspace.db_path.parent.exists()
    assert workspace.log_dir.exists()


def test_prepare_workspace_is_idempotent(tmp_path: Path) -> None:
    first = prepare_workspace(tmp_path / "fake")
    first.config_path.write_text(json.dumps({"mode": "roguelike", "code": "9999"}))
    second = prepare_workspace(tmp_path / "fake")
    # Re-preparing an existing workspace leaves what's there alone.
    assert json.loads(second.config_path.read_text())["code"] == "9999"


# -- synthetic calls: real sessions everywhere below Asterisk -----------------


def _run_harness(tmp_path: Path, *, cycles: int = 1, **kwargs: Any) -> list[Any]:
    """Run the harness to completion and return the persisted records, oldest first."""

    async def run() -> list[Any]:
        workspace = prepare_workspace(tmp_path / "fake")
        harness = build_fake_harness(workspace, pacing=INSTANT, interval_s=0.0, **kwargs)
        await harness.engine.start()
        try:
            await harness.run(cycles=cycles)
        finally:
            await harness.engine.aclose()
        records = await harness.store.query()
        return list(reversed(records))  # query() is newest-first

    return asyncio.run(run())


def test_every_mode_runs_end_to_end(tmp_path: Path) -> None:
    records = _run_harness(tmp_path)
    assert {r.mode for r in records} == {"tweeted", "puzzle", "roguelike"}


def test_every_terminal_outcome_is_reached(tmp_path: Path) -> None:
    records = _run_harness(tmp_path)
    assert {"succeed", "exile", "hangup"} <= {r.outcome for r in records}


def test_synthetic_sessions_persist_like_real_ones(tmp_path: Path) -> None:
    records = _run_harness(tmp_path)
    assert len(records) == len(DEFAULT_SCENARIOS)
    for record in records:
        assert record.session_id
        assert record.caller_id  # the fake dials from a number, like a caller
        assert record.ended_at >= record.started_at
        assert record.duration_seconds >= 0
    # Per-mode detail lands in the JSON column exactly as a real puzzle call's does.
    puzzle = next(r for r in records if r.mode == "puzzle")
    assert puzzle.detail["puzzle_id"].endswith(".wav")


def test_each_scenario_reaches_its_expected_outcome(tmp_path: Path) -> None:
    records = _run_harness(tmp_path)
    for scenario, record in zip(DEFAULT_SCENARIOS, records, strict=True):
        assert record.mode == scenario.mode
        assert record.outcome in scenario.expect, scenario.name


def test_cycles_repeat_the_whole_scenario_matrix(tmp_path: Path) -> None:
    records = _run_harness(tmp_path, cycles=2)
    assert len(records) == 2 * len(DEFAULT_SCENARIOS)


def test_a_scenario_drives_global_config_for_its_call(tmp_path: Path) -> None:
    """The harness changes Mode and Code between calls the way an Operator would."""
    scenario = Scenario(
        name="custom",
        mode="tweeted",
        code="4711",
        dtmf=("4711",),
        caller_id="+15550001111",
        expect=frozenset({"succeed"}),
    )
    records = _run_harness(tmp_path, scenarios=(scenario,))
    assert len(records) == 1
    assert records[0].outcome == "succeed"  # judged against the scenario's Code


# -- synthetic audio while a call is live ------------------------------------


def test_audio_frames_are_emitted_for_synthetic_calls(tmp_path: Path) -> None:
    async def run() -> tuple[list[bytes], int]:
        workspace = prepare_workspace(tmp_path / "fake")
        harness = build_fake_harness(
            workspace,
            pacing=fake_pbx.Pacing(play_s=0.0, dtmf_s=0.05),
            interval_s=0.0,
            scenarios=DEFAULT_SCENARIOS[:1],
        )
        frames: list[bytes] = []
        harness.pbx.subscribe_audio(frames.append)
        await harness.engine.start()
        try:
            await harness.run(cycles=1)
        finally:
            await harness.engine.aclose()
        during = len(frames)
        await asyncio.sleep(0.05)  # the stream stops with the call
        return frames, during

    frames, during = asyncio.run(run())
    assert during >= 1
    assert len(frames) == during
    assert all(len(f) == FRAME_BYTES for f in frames)


def test_unsubscribing_stops_delivery() -> None:
    async def run() -> tuple[int, int]:
        frames: list[bytes] = []
        pbx = FakePBX()
        unsubscribe = pbx.subscribe_audio(frames.append)
        pbx.start_audio("chan-1")
        await asyncio.sleep(0.05)
        heard = len(frames)
        unsubscribe()
        await asyncio.sleep(0.05)
        after = len(frames)
        await pbx.stop_audio()
        return heard, after

    heard, after = asyncio.run(run())
    assert heard >= 1
    assert after == heard


def test_no_audio_is_generated_with_nobody_listening() -> None:
    """ADR-0003: the media path is spun up on demand, not once per call."""

    async def run() -> int:
        pbx = FakePBX()
        pbx.start_audio("chan-1")
        await asyncio.sleep(0.05)
        return await pbx.stop_audio()

    assert asyncio.run(run()) == 0


def test_subscribing_mid_call_starts_the_media_path() -> None:
    async def run() -> list[bytes]:
        frames: list[bytes] = []
        pbx = FakePBX()
        pbx.start_audio("chan-1")
        await asyncio.sleep(0.02)  # the call is already under way
        pbx.subscribe_audio(frames.append)
        await asyncio.sleep(0.05)
        await pbx.stop_audio()
        return frames

    frames = asyncio.run(run())
    assert frames  # a consumer that attaches late still hears the rest
    assert all(len(f) == FRAME_BYTES for f in frames)


def test_audio_stops_when_the_call_ends() -> None:
    async def run() -> tuple[int, int, int]:
        frames: list[bytes] = []
        pbx = FakePBX()
        pbx.subscribe_audio(frames.append)
        pbx.start_audio("chan-1")
        await asyncio.sleep(0.05)
        sent = await pbx.stop_audio()
        settled = len(frames)
        await asyncio.sleep(0.05)
        return sent, settled, len(frames)

    sent, settled, later = asyncio.run(run())
    assert settled >= 1
    assert sent == settled  # every frame the stream counted reached the sink
    assert later == settled


def test_successive_calls_get_their_own_tone() -> None:
    """Each synthetic call sounds different, so a dev can hear the call change."""

    async def run() -> tuple[bytes, bytes]:
        pbx = FakePBX()
        captured: list[bytes] = []
        pbx.subscribe_audio(captured.append)
        pbx.start_audio("chan-1")
        await asyncio.sleep(0.03)
        await pbx.stop_audio()
        first = captured[0]
        captured.clear()
        pbx.start_audio("chan-2")
        await asyncio.sleep(0.03)
        await pbx.stop_audio()
        return first, captured[0]

    first, second = asyncio.run(run())
    assert first != second


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
