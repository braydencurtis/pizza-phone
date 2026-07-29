"""Tests for the ARI → ``core.CallIO`` adapter.

Two layers:

- **Mapping** — each sync ``CallIO`` method schedules the right async ARI
  command onto the engine's event loop.
- **DoD** — an unmodified ``core.flow`` mode handler runs to completion against
  the adapter. The flow is synchronous and the ARI client is asynchronous, so
  the adapter is exercised exactly as the engine will drive it: the handler runs
  in a worker thread (``asyncio.to_thread``) while the loop stays free to service
  the coroutines the adapter submits back to it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from core import flow
from core.router import Router
from engine.ari_call_io import ARICallIO


class FakeARI:
    """Records the async ARI commands the adapter issues.

    ``dtmf`` scripts successive ``read_digits`` returns; an exhausted script
    yields ``""`` (the caller-hung-up signal ``core.flow`` recognises).
    """

    def __init__(self, dtmf: list[str] | None = None) -> None:
        self._dtmf = list(dtmf or [])
        self.calls: list[tuple[Any, ...]] = []

    async def play(self, channel_id: str, media: str, *, timeout: float | None = None) -> None:
        self.calls.append(("play", channel_id, media))

    async def read_digits(
        self, channel_id: str, num_digits: int, inter_digit_timeout_ms: int
    ) -> str:
        self.calls.append(("read_digits", channel_id, num_digits, inter_digit_timeout_ms))
        return self._dtmf.pop(0) if self._dtmf else ""

    async def hangup(self, channel_id: str) -> None:
        self.calls.append(("hangup", channel_id))

    async def continue_in_dialplan(
        self,
        channel_id: str,
        context: str | None = None,
        extension: str | None = None,
        priority: int | None = None,
    ) -> None:
        self.calls.append(("continue", channel_id, context, extension, priority))

    async def set_channel_var(self, channel_id: str, variable: str, value: str) -> None:
        self.calls.append(("set_var", channel_id, variable, value))


class FakeTTS:
    """A TTS backend that writes a placeholder WAV so ``speak`` has a file to play."""

    def synthesize(self, text: str, output_path: Path) -> None:
        output_path.write_bytes(b"RIFF")


def _io(ari: FakeARI, loop: asyncio.AbstractEventLoop, **kwargs: Any) -> ARICallIO:
    # FakeARI is a structural stand-in for ARIClient (only the methods the
    # adapter calls), so the concrete type hint doesn't match.
    return ARICallIO(ari, "chan-1", loop, upstream_ext="6001", **kwargs)  # type: ignore[arg-type]


# -- method → ARI command mapping ---------------------------------------------


def test_play_maps_to_ari_play() -> None:
    async def run() -> list[tuple[Any, ...]]:
        ari = FakeARI()
        io = _io(ari, asyncio.get_running_loop())
        await asyncio.to_thread(io.play, "sound:riddle")
        return ari.calls

    assert asyncio.run(run()) == [("play", "chan-1", "sound:riddle")]


def test_play_passes_the_configured_timeout_to_ari() -> None:
    async def run() -> float | None:
        seen: dict[str, float | None] = {}

        class TimeoutARI(FakeARI):
            async def play(self, channel_id: str, media: str, *, timeout: float | None = None) -> None:
                seen["timeout"] = timeout

        io = _io(TimeoutARI(), asyncio.get_running_loop(), play_timeout_s=12.5)
        await asyncio.to_thread(io.play, "sound:x")
        return seen["timeout"]

    # The adapter must hand its bound down so a lost PlaybackFinished can't wedge
    # the call (see ARICallIO.DEFAULT_PLAY_TIMEOUT_S).
    assert asyncio.run(run()) == 12.5


def test_read_dtmf_returns_collected_digits() -> None:
    async def run() -> tuple[str, list[tuple[Any, ...]]]:
        ari = FakeARI(dtmf=["1234"])
        io = _io(ari, asyncio.get_running_loop())
        digits = await asyncio.to_thread(io.read_dtmf, 4, 5000)
        return digits, ari.calls

    digits, calls = asyncio.run(run())
    assert digits == "1234"
    assert calls == [("read_digits", "chan-1", 4, 5000)]


def test_hangup_maps_to_ari_hangup() -> None:
    async def run() -> list[tuple[Any, ...]]:
        ari = FakeARI()
        io = _io(ari, asyncio.get_running_loop())
        await asyncio.to_thread(io.hangup)
        return ari.calls

    assert asyncio.run(run()) == [("hangup", "chan-1")]


def test_to_success_sets_upstream_ext_then_continues_to_success_context() -> None:
    async def run() -> list[tuple[Any, ...]]:
        ari = FakeARI()
        io = _io(ari, asyncio.get_running_loop())
        await asyncio.to_thread(io.to_success)
        return ari.calls

    # The dialplan's pizza-success context dials PJSIP/${UPSTREAM_EXT}, so the
    # variable must be set before the channel leaves Stasis.
    assert asyncio.run(run()) == [
        ("set_var", "chan-1", "UPSTREAM_EXT", "6001"),
        ("continue", "chan-1", "pizza-success", "s", 1),
    ]


def test_speak_synthesizes_then_plays_the_audio_as_a_sound_uri(tmp_path: Path) -> None:
    async def run() -> list[tuple[Any, ...]]:
        ari = FakeARI()
        io = _io(ari, asyncio.get_running_loop(), tts=FakeTTS(), output_dir=tmp_path)
        await asyncio.to_thread(io.speak, "you are in the backrooms")
        return ari.calls

    calls = asyncio.run(run())
    assert len(calls) == 1
    verb, channel_id, media = calls[0]
    assert (verb, channel_id) == ("play", "chan-1")
    # ARI plays via a sound: URI naming a resource without its extension.
    assert media.startswith(f"sound:{tmp_path}/")
    assert not media.endswith(".wav")
    # The synthesized file was actually written where the URI points.
    assert Path(media[len("sound:") :] + ".wav").exists()


def test_read_dtmf_blocks_the_worker_until_the_loop_resolves_the_read() -> None:
    """The bridge's core claim: while the ARI coroutine suspends on an event,
    the worker thread stays blocked and the loop stays free to resolve it."""

    async def run() -> str:
        loop = asyncio.get_running_loop()
        digit_arrived = asyncio.Event()

        class SuspendingARI(FakeARI):
            async def read_digits(
                self, channel_id: str, num_digits: int, inter_digit_timeout_ms: int
            ) -> str:
                await digit_arrived.wait()  # genuinely suspends on the loop
                return "7"

        io = _io(SuspendingARI(), loop)
        reader = asyncio.create_task(asyncio.to_thread(io.read_dtmf, 1, 5000))
        await asyncio.sleep(0.05)
        assert not reader.done()  # worker is blocked on the loop-side await
        digit_arrived.set()  # the loop was free to receive this
        return await asyncio.wait_for(reader, timeout=1)

    assert asyncio.run(run()) == "7"


# -- DoD: an unmodified core.flow handler runs against the adapter -------------


def _router(tmp_path: Path, *, mode: str, code: str, attempt_limit: int = 3) -> Router:
    config_dir = tmp_path / "config"
    log_dir = tmp_path / "logs"
    config_dir.mkdir()
    log_dir.mkdir()
    (config_dir / "mode.json").write_text(
        json.dumps({"mode": mode, "code": code, "attempt_limit": attempt_limit})
    )
    return Router(config_dir=config_dir, log_dir=log_dir)


def test_puzzle_flow_runs_to_success_against_the_adapter(tmp_path: Path) -> None:
    async def run() -> tuple[dict[str, Any], list[tuple[Any, ...]]]:
        ari = FakeARI(dtmf=["4242"])
        io = _io(ari, asyncio.get_running_loop())
        router = _router(tmp_path, mode="puzzle", code="4242")
        result = await asyncio.to_thread(
            flow.run_puzzle,
            io,
            router,
            code="4242",
            max_attempts=3,
            puzzle_id="riddle-001.wav",
            prompt_media="sound:puzzles/riddle-001",
            exile_media="sound:voicemail/busy",
            wrong_media="sound:beep",
        )
        return result, ari.calls

    result, calls = asyncio.run(run())
    assert result["outcome"] == "succeed"
    # prompt played, answer read, then routed onto the success path.
    assert calls == [
        ("play", "chan-1", "sound:puzzles/riddle-001"),
        ("read_digits", "chan-1", 4, flow.PUZZLE_TIMEOUT_MS),
        ("set_var", "chan-1", "UPSTREAM_EXT", "6001"),
        ("continue", "chan-1", "pizza-success", "s", 1),
    ]


def test_tweeted_flow_exiles_and_hangs_up_against_the_adapter(tmp_path: Path) -> None:
    async def run() -> tuple[dict[str, Any], list[tuple[Any, ...]]]:
        ari = FakeARI(dtmf=["0000", "0000", "0000"])
        io = _io(ari, asyncio.get_running_loop())
        router = _router(tmp_path, mode="tweeted", code="1234")
        result = await asyncio.to_thread(
            flow.run_tweeted,
            io,
            router,
            code="1234",
            max_attempts=3,
            exile_media="sound:voicemail/busy",
            wrong_media="sound:beep",
        )
        return result, ari.calls

    result, calls = asyncio.run(run())
    assert result["outcome"] == "exile"
    # two wrong answers beep, the third exiles then hangs up; never succeeds.
    assert ("play", "chan-1", "sound:voicemail/busy") in calls
    assert calls[-1] == ("hangup", "chan-1")
    assert not any(c[0] == "continue" for c in calls)


def test_roguelike_flow_runs_against_the_adapter(tmp_path: Path) -> None:
    async def run() -> dict[str, Any]:
        ari = FakeARI(dtmf=["1"] * 40)
        io = _io(ari, asyncio.get_running_loop())
        router = _router(tmp_path, mode="roguelike", code="0000")
        return await asyncio.to_thread(flow.run_roguelike, io, router, code="0000")

    result = asyncio.run(run())
    assert result["mode"] == "roguelike"
