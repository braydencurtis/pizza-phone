"""The Fake PBX: a scripted stand-in for Asterisk that drives the real engine.

Phase 2 is built at the kitchen table, not in the booth (CONTEXT.md, "Phase 2
development"). This module is what makes that possible: a stand-in for Asterisk
that speaks the same surface :class:`~engine.ari_client.ARIClient` does, fires
synthetic Call Sessions at the real :class:`~engine.call_engine.CallEngine` on a
timer, and emits synthetic call audio. Everything below Asterisk is genuine —
the real mode handlers run through the real ``CallIO`` seam and the completed
sessions land in a real :class:`~engine.call_store.CallStore`. Only the PBX is
pretend.

**Development only.** Nothing here is reachable in a live setup: the harness is
started by an explicit ``--fake-pbx`` flag on ``python -m engine`` and by
nothing else — no environment variable, no config key, no default (see
``engine/__main__.py``, which also refuses fake mode when a real ARI connection
is configured).

Three pieces:

- :class:`FakePBX` — the ARI stand-in. Answers, plays, dials scripted DTMF a key
  at a time, and injects ``StasisStart`` and ``ChannelDtmfReceived`` the way the
  real client's reader would. Promoted from the structural fake the engine tests
  were already written against, so the harness and the test suite exercise one
  fake, not two.
- The synthetic media path (``engine/fake_audio.py``) — the frames a Listen-in
  consumer will receive, standing in for ARI Snoop → ExternalMedia. This is the
  half of Listen-in that needs no hardware.
- :class:`FakePBXHarness` — the scenario matrix. Sets Global Config, places the
  call, streams its audio, and reads the persisted record back to check the
  session ended where the scenario said it would.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import (
    CONFIG_FILENAME,
    DEFAULT_ATTEMPT_LIMIT,
    DEFAULT_UPSTREAM_EXTENSION,
    write_config,
)
from core.types import Mode, Outcome
from engine.ari_client import CHANNEL_DTMF_RECEIVED, STASIS_END, STASIS_START, EventHandler
from engine.call_engine import CallEngine
from engine.call_store import CallRecord, CallStore
from engine.fake_audio import (
    FRAME_BYTES,
    SAMPLES_PER_FRAME,
    TONE_STEPS_HZ,
    AudioSink,
    SyntheticAudioStream,
    slin_frame,
    write_wav,
)

logger = logging.getLogger(__name__)

# -- the fake PBX ------------------------------------------------------------


@dataclass(frozen=True)
class Pacing:
    """How long the fake dawdles over caller-facing operations.

    Zero (:data:`INSTANT`) is what tests want; the harness runs at
    :data:`LIFELIKE` so a synthetic call takes about as long as a real one and
    the Console has something to render for more than a millisecond.
    """

    play_s: float = 0.0
    dtmf_s: float = 0.0


INSTANT = Pacing()
LIFELIKE = Pacing(play_s=1.5, dtmf_s=2.5)


class SilentTTS:
    """A TTS backend that writes silent WAVs.

    The roguelike speaks every node, and a dev laptop has no reason to have
    espeak or ``say`` installed — so the fake brings its own backend rather than
    making the harness depend on one. The fake never renders audio to a caller
    anyway; what a listener hears is the synthetic media path above.
    """

    def synthesize(self, text: str, output_path: Path) -> None:
        write_wav(output_path, [bytes(FRAME_BYTES)])


class FakePBX:
    """A scripted stand-in for Asterisk, speaking the ``ARIClient`` surface.

    Structural, not a subclass: it offers exactly the methods the engine and
    :class:`~engine.ari_call_io.ARICallIO` call, and nothing else. ``dtmf``
    scripts successive ``read_digits`` returns for the current call; an
    exhausted script yields ``""``, the caller-hung-up signal ``core.flow``
    recognises.

    Every command is recorded in :attr:`calls` — the engine tests assert against
    that log, and it doubles as a trace when a synthetic call misbehaves.
    """

    def __init__(self, dtmf: Sequence[str] | None = None, *, pacing: Pacing = INSTANT) -> None:
        self._dtmf = list(dtmf or [])
        # Public and mutable: pacing is a dev knob, not internal state — a
        # harness run can be slowed to watch the Console or dropped to zero.
        self.pacing = pacing
        self.calls: list[tuple[Any, ...]] = []
        self._handlers: dict[str, list[EventHandler]] = {}
        self._audio_sinks: list[AudioSink] = []
        self._audio: SyntheticAudioStream | None = None
        # The channel the current synthetic call is on, so a Listen-in consumer
        # can tell which call it is hearing; None between calls.
        self.live_channel: str | None = None
        self._tone_index = 0

    # -- surface the engine's wiring uses ---------------------------------

    def on(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        """Register ``handler`` for ``event_type``; returns an unsubscribe callable."""
        self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def connect(self) -> None:
        """Stand in for opening the REST session and event socket."""
        self.calls.append(("connect",))

    async def close(self) -> None:
        """Stand in for disconnecting; stops any audio still streaming."""
        await self.stop_audio()
        self.calls.append(("close",))

    async def answer(self, channel_id: str) -> None:
        """Answer an inbound channel."""
        self.calls.append(("answer", channel_id))

    # -- surface ARICallIO uses -------------------------------------------

    async def play(self, channel_id: str, media: str, *, timeout: float | None = None) -> None:
        """Play ``media``, taking :attr:`pacing`'s time over it as a real prompt would."""
        self.calls.append(("play", channel_id, media))
        await self._pause(self.pacing.play_s)

    async def read_digits(
        self, channel_id: str, num_digits: int, inter_digit_timeout_ms: int
    ) -> str:
        """Hand back the next scripted entry, or ``""`` once the script runs out.

        ``""`` is what the real client returns when a caller enters nothing, and
        what ``core.flow`` reads as a hangup — so an exhausted script is how a
        synthetic caller walks away.

        The digits are *dialled* rather than simply returned: each one is
        delivered as a ``ChannelDtmfReceived`` event first, the way the real
        client's reader would, so anything watching the wire (the Console's live
        digit display) sees a synthetic caller press keys one at a time.
        """
        self.calls.append(("read_digits", channel_id, num_digits))
        entry = self._dtmf.pop(0) if self._dtmf else ""
        if not entry:
            # A caller sitting in silence: the read times out having heard
            # nothing, which is what the empty string means to ``core.flow``.
            await self._pause(self.pacing.dtmf_s)
            return ""
        per_digit = self.pacing.dtmf_s / len(entry)
        for digit in entry:
            await self._pause(per_digit)
            await self.fire_dtmf(channel_id, digit)
        return entry

    async def hangup(self, channel_id: str) -> None:
        """Tear down a channel."""
        self.calls.append(("hangup", channel_id))

    async def set_channel_var(self, channel_id: str, variable: str, value: str) -> None:
        """Set a channel variable (the engine sets ``UPSTREAM_EXT`` before a success)."""
        self.calls.append(("set_var", channel_id, variable, value))

    async def continue_in_dialplan(
        self,
        channel_id: str,
        context: str | None = None,
        extension: str | None = None,
        priority: int | None = None,
    ) -> None:
        """Exit Stasis for the dialplan — where a winning caller is Handed Off."""
        self.calls.append(("continue", channel_id, context))

    # -- driving the fake --------------------------------------------------

    def script(self, dtmf: Sequence[str]) -> None:
        """Replace the DTMF script — what the next synthetic caller dials."""
        self._dtmf = list(dtmf)

    async def fire_stasis_start(self, channel_id: str, number: str | None = None) -> None:
        """Deliver a ``StasisStart`` the way ``ARIClient``'s reader would.

        Handlers run inline here, as they do on the real reader task, so a
        handler that blocked would stall the fake exactly as it would stall the
        real client — the property the engine's non-blocking handler depends on.
        """
        event = {
            "type": STASIS_START,
            "channel": {"id": channel_id, "caller": {"number": number or ""}},
        }
        for handler in list(self._handlers.get(STASIS_START, ())):
            result = handler(event)
            if inspect.isawaitable(result):
                await result

    async def fire_dtmf(self, channel_id: str, digit: str) -> None:
        """Deliver one ``ChannelDtmfReceived`` the way ``ARIClient``'s reader would."""
        event = {
            "type": CHANNEL_DTMF_RECEIVED,
            "channel": {"id": channel_id},
            "digit": digit,
        }
        for handler in list(self._handlers.get(CHANNEL_DTMF_RECEIVED, ())):
            result = handler(event)
            if inspect.isawaitable(result):
                await result

    async def fire_channel_gone(self, channel_id: str) -> None:
        """Deliver the ``StasisEnd`` a caller hanging up produces.

        On a real PBX this is what arrives the moment the handset goes down, and
        it is how the engine tells the caller leaving from its own failure.
        """
        event = {"type": STASIS_END, "channel": {"id": channel_id}}
        for handler in list(self._handlers.get(STASIS_END, ())):
            result = handler(event)
            if inspect.isawaitable(result):
                await result

    # -- the synthetic media path -----------------------------------------

    def subscribe_audio(self, sink: AudioSink) -> Callable[[], None]:
        """Receive the live call's audio frames; returns an unsubscribe callable.

        The seam a Listen-in consumer attaches to. Subscribing during a call
        starts the media path there and then: ADR-0003 has the Snoop machinery
        "spun up on demand rather than per call", so with nobody listening the
        fake generates nothing, and a consumer that attaches mid-call still
        hears the rest of it.
        """
        self._audio_sinks.append(sink)
        if self.live_channel is not None:
            self._start_stream()

        def unsubscribe() -> None:
            if sink in self._audio_sinks:
                self._audio_sinks.remove(sink)

        return unsubscribe

    def start_audio(self, channel_id: str) -> None:
        """Mark ``channel_id`` as the live call, and stream if anyone is listening."""
        self.live_channel = channel_id
        if self._audio_sinks:
            self._start_stream()

    async def stop_audio(self) -> int:
        """End the live call's media; returns how many frames it sent."""
        self.live_channel = None
        if self._audio is None:
            return 0
        stream, self._audio = self._audio, None
        await stream.stop()
        return stream.frames_sent

    def _start_stream(self) -> None:
        if self._audio is not None:
            return
        frequency = TONE_STEPS_HZ[self._tone_index % len(TONE_STEPS_HZ)]
        self._tone_index += 1
        logger.debug(
            "Fake PBX: streaming %.0f Hz audio for %s", frequency, self.live_channel
        )
        self._audio = SyntheticAudioStream(self._emit_audio, frequency_hz=frequency)
        self._audio.start()

    def _emit_audio(self, frame: bytes) -> None:
        for sink in list(self._audio_sinks):
            try:
                sink(frame)
            except Exception:
                logger.exception("Fake PBX audio sink failed; dropping the frame")

    async def _pause(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


# -- the scenario matrix -----------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """One synthetic Call Session: the Global Config it runs under, and the
    caller who dials into it.

    ``expect`` is the set of outcomes the scenario is supposed to produce. The
    harness only logs a mismatch — it is a dev tool, not a test — but a run that
    starts warning is the signal that a change broke a Mode.
    """

    name: str
    mode: Mode
    code: str
    dtmf: tuple[str, ...]
    caller_id: str
    expect: frozenset[Outcome]


DEFAULT_INTERVAL_S = 8.0

# Every Mode, and every terminal outcome a caller can reach: dial the code and
# win, burn the attempt limit and be Exiled, or pick up and say nothing.
# Roguelike has no attempt limit and no code entry, so it has two paths rather
# than three — the walk, and the caller who goes quiet in it (#53).
DEFAULT_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="tweeted-success",
        mode="tweeted",
        code="1234",
        dtmf=("1234",),
        caller_id="+15550001001",
        expect=frozenset({"succeed"}),
    ),
    Scenario(
        name="tweeted-wrong-then-right",
        mode="tweeted",
        code="1234",
        dtmf=("9999", "1234"),
        caller_id="+15550001002",
        expect=frozenset({"succeed"}),
    ),
    Scenario(
        name="tweeted-exile",
        mode="tweeted",
        code="1234",
        dtmf=("9999", "8888", "7777"),
        caller_id="+15550001003",
        expect=frozenset({"exile"}),
    ),
    Scenario(
        name="tweeted-hangup",
        mode="tweeted",
        code="1234",
        dtmf=(),
        caller_id="+15550001004",
        expect=frozenset({"hangup"}),
    ),
    Scenario(
        name="puzzle-success",
        mode="puzzle",
        code="4242",
        dtmf=("4242",),
        caller_id="+15550002001",
        expect=frozenset({"succeed"}),
    ),
    Scenario(
        name="puzzle-exile",
        mode="puzzle",
        code="4242",
        dtmf=("1111", "2222", "3333"),
        caller_id="+15550002002",
        expect=frozenset({"exile"}),
    ),
    Scenario(
        name="puzzle-hangup",
        mode="puzzle",
        code="4242",
        dtmf=(),
        caller_id="+15550002003",
        expect=frozenset({"hangup"}),
    ),
    Scenario(
        name="roguelike-walk",
        mode="roguelike",
        code="8675",
        dtmf=("1",) * 40,
        caller_id="+15550003001",
        # Either ending is a pass. The tree is regenerated per Call Session, and
        # a caller pressing one key follows a fixed chain through the rooms that
        # usually closes into a loop — so this synthetic caller is Exiled about
        # two runs in three and finds the Code in the other (#59).
        expect=frozenset({"succeed", "exile"}),
    ),
    Scenario(
        name="roguelike-hangup",
        mode="roguelike",
        code="8675",
        dtmf=(),
        caller_id="+15550003002",
        expect=frozenset({"hangup"}),
    ),
)


# -- the dev workspace -------------------------------------------------------


@dataclass(frozen=True)
class FakeWorkspace:
    """Where a fake-mode run keeps its state — never the booth's.

    Synthetic calls rewrite Global Config between scenarios and fill the call
    store with invented history, so fake mode gets its own config, logs, puzzle
    pool and database. The live ``config/`` and call store are never touched.
    """

    root: Path
    config_dir: Path
    log_dir: Path
    audio_dir: Path
    db_path: Path
    listen_in_dir: Path

    @property
    def config_path(self) -> Path:
        return self.config_dir / CONFIG_FILENAME


def prepare_workspace(root: Path) -> FakeWorkspace:
    """Create (or reuse) the fake-mode workspace under ``root``.

    Idempotent: an existing puzzle pool is left alone, so a dev can have the
    harness play real riddles by dropping WAVs into ``<root>/audio/puzzles``.
    Config is only seeded, not preserved in any meaningful sense — the harness
    rewrites ``mode.json`` before every synthetic call, so hand-editing it
    between runs achieves nothing.
    """
    workspace = FakeWorkspace(
        root=root,
        config_dir=root / "config",
        log_dir=root / "logs",
        audio_dir=root / "audio",
        db_path=root / "calls.db",
        listen_in_dir=root / "listen-in",
    )
    for directory in (workspace.config_dir, workspace.log_dir, workspace.audio_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if not workspace.config_path.exists():
        write_config(workspace.config_path, global_config(DEFAULT_SCENARIOS[0]))

    pool = workspace.audio_dir / "puzzles"
    pool.mkdir(parents=True, exist_ok=True)
    if not any(pool.glob("*.wav")):
        # Puzzle mode picks from the pool at pickup, so the fake seeds one. It
        # is a real (if dull) WAV — a second of tone — rather than a stub, so
        # anything that looks at the file finds audio.
        write_wav(
            pool / "fake-riddle-001.wav",
            [slin_frame(i * SAMPLES_PER_FRAME) for i in range(50)],
        )
    return workspace


def global_config(scenario: Scenario) -> dict[str, Any]:
    """The Global Config a scenario's Call Session is judged against.

    One shape, whether it is seeding a fresh workspace or being written ahead of
    the next synthetic call — the same keys ``config/mode.json`` carries.
    """
    return {
        "mode": scenario.mode,
        "code": scenario.code,
        "attempt_limit": DEFAULT_ATTEMPT_LIMIT,
        "upstream_extension": DEFAULT_UPSTREAM_EXTENSION,
        "tts_backend": None,
    }


# -- the harness -------------------------------------------------------------


class FakePBXHarness:
    """Fires synthetic Call Sessions at the engine, one scenario at a time.

    Each scenario writes its Global Config, places a call, streams that call's
    audio for as long as it lasts, and then reads the persisted record back — so
    a run is a continuous, hands-off demonstration that the whole stack below
    Asterisk still works.
    """

    def __init__(
        self,
        pbx: FakePBX,
        engine: CallEngine,
        store: CallStore,
        *,
        config_path: Path,
        scenarios: Sequence[Scenario] = DEFAULT_SCENARIOS,
        interval_s: float = DEFAULT_INTERVAL_S,
    ) -> None:
        self.pbx = pbx
        self.engine = engine
        self.store = store
        self.scenarios = tuple(scenarios)
        self._config_path = config_path
        self._interval_s = interval_s
        self._placed = 0

    async def run(self, cycles: int = 0) -> None:
        """Run the scenario matrix ``cycles`` times, or forever when ``cycles`` is 0."""
        logger.info(
            "Fake PBX: %d scenarios, %.1fs apart — development only, no Asterisk attached",
            len(self.scenarios),
            self._interval_s,
        )
        cycle = 0
        while cycles == 0 or cycle < cycles:
            cycle += 1
            await self.run_cycle()

    async def run_cycle(self) -> None:
        """Run every scenario once, pausing ``interval_s`` between calls."""
        for scenario in self.scenarios:
            await self.run_scenario(scenario)
            if self._interval_s > 0:
                await asyncio.sleep(self._interval_s)

    async def run_scenario(self, scenario: Scenario) -> CallRecord | None:
        """Place one synthetic call and return the session it persisted."""
        self._write_config(scenario)
        self.pbx.script(scenario.dtmf)

        self._placed += 1
        channel_id = f"fake-{self._placed:04d}"
        logger.info(
            "Fake PBX: placing %s on %s (mode=%s code=%s)",
            scenario.name,
            channel_id,
            scenario.mode,
            scenario.code,
        )
        await self.pbx.fire_stasis_start(channel_id, number=scenario.caller_id)
        # The engine claims the slot synchronously, so the session is available
        # the moment the event has been delivered — that is how we know which
        # record to read back once the call is over. A session for some other
        # channel means the engine was busy and refused this call.
        session = self.engine.active_session
        session_id = None
        if session is None or session.channel_id != channel_id:
            logger.warning("Fake PBX: %s was not accepted by the engine", scenario.name)
        else:
            session_id = session.session_id

        self.pbx.start_audio(channel_id)
        try:
            await self.engine.wait_for_idle()
        finally:
            frames = await self.pbx.stop_audio()

        record = await self.store.get(session_id) if session_id else None
        self._report(scenario, record, frames)
        return record

    # -- internals ---------------------------------------------------------

    def _write_config(self, scenario: Scenario) -> None:
        """Set Global Config for the call about to be placed."""
        write_config(self._config_path, global_config(scenario))

    def _report(self, scenario: Scenario, record: CallRecord | None, frames: int) -> None:
        if record is None:
            logger.warning("Fake PBX: %s persisted no session", scenario.name)
            return
        if record.outcome not in scenario.expect:
            logger.warning(
                "Fake PBX: %s ended %s, expected one of %s",
                scenario.name,
                record.outcome,
                ", ".join(sorted(scenario.expect)),
            )
            return
        logger.info(
            "Fake PBX: %s → %s in %.1fs (%d attempts, %d audio frames)",
            scenario.name,
            record.outcome,
            record.duration_seconds,
            record.attempts,
            frames,
        )


def build_fake_harness(
    workspace: FakeWorkspace,
    *,
    pacing: Pacing = LIFELIKE,
    scenarios: Sequence[Scenario] = DEFAULT_SCENARIOS,
    interval_s: float = DEFAULT_INTERVAL_S,
) -> FakePBXHarness:
    """Wire a real Call Engine to a Fake PBX over the workspace's directories.

    The one wiring both ``python -m engine --fake-pbx`` and the harness tests
    use, so what a dev runs is what the tests cover. The engine (``.engine``) is
    returned unstarted; ``start()`` it before running the harness.
    """
    pbx = FakePBX(pacing=pacing)
    store = CallStore(workspace.db_path)
    engine = CallEngine(
        pbx,  # type: ignore[arg-type]  # structural stand-in for ARIClient
        store,
        config_dir=workspace.config_dir,
        log_dir=workspace.log_dir,
        audio_dir=workspace.audio_dir,
        tts=SilentTTS(),
    )
    return FakePBXHarness(
        pbx,
        engine,
        store,
        config_path=workspace.config_path,
        scenarios=scenarios,
        interval_s=interval_s,
    )
