"""The Fake PBX's media path: synthetic call audio for a Listen-in consumer.

Listen-in carries 8 kHz 16-bit mono signed linear ("slin") audio out of
Asterisk — ARI Snoop into ExternalMedia, per ADR-0002 — so that is exactly what
this module produces: 20 ms frames of 160 samples / 320 bytes, the frame size
ExternalMedia delivers, paced in wall-clock time. It is what lets the browser
half of Listen-in be built with no hardware attached; only the Snoop →
ExternalMedia half needs the rig.

Development only — it is reached solely through ``engine/fake_pbx.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import struct
import wave
from collections.abc import Callable, Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

SAMPLE_RATE_HZ = 8000
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE_HZ * FRAME_MS // 1000
FRAME_BYTES = SAMPLES_PER_FRAME * 2

# A plain tone, wobbled by a slow tremolo so a listener can tell live audio from
# a stuck buffer by ear. Peak amplitude leaves generous headroom — this plays
# into someone's laptop speakers, unattended, for as long as the harness runs.
TONE_PEAK = 6000
TREMOLO_HZ = 3.0
# Successive synthetic calls step through these, so a new call is audible as a
# new call rather than an unbroken drone.
TONE_STEPS_HZ = (392.0, 440.0, 494.0, 523.0)

AudioSink = Callable[[bytes], None]


def slin_frame(start_sample: int, *, frequency_hz: float = TONE_STEPS_HZ[0]) -> bytes:
    """One 20 ms slin16 frame of tone, starting at ``start_sample``.

    Pure and absolute-time indexed: the caller advances ``start_sample`` by
    :data:`SAMPLES_PER_FRAME` per frame, and the waveform stays phase-continuous
    across frame boundaries instead of clicking every 20 ms.
    """
    values = []
    for n in range(start_sample, start_sample + SAMPLES_PER_FRAME):
        t = n / SAMPLE_RATE_HZ
        envelope = 0.6 + 0.4 * math.sin(2 * math.pi * TREMOLO_HZ * t)
        values.append(int(TONE_PEAK * envelope * math.sin(2 * math.pi * frequency_hz * t)))
    return struct.pack(f"<{SAMPLES_PER_FRAME}h", *values)


class SyntheticAudioStream:
    """Paces slin16 frames to a sink in real time, for the life of one call.

    Wall-clock pacing is the point: a Listen-in consumer is fed audio as fast as
    the caller generates it, so the fake must not sprint. Frames are emitted
    against a deadline rather than a plain sleep, so the stream doesn't drift
    slower and slower over a long call.
    """

    def __init__(self, sink: AudioSink, *, frequency_hz: float = TONE_STEPS_HZ[0]) -> None:
        self._sink = sink
        self._frequency_hz = frequency_hz
        self._task: asyncio.Task[None] | None = None
        self.frames_sent = 0

    def start(self) -> None:
        """Begin streaming on a background task."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="fake-pbx-audio")

    async def stop(self) -> None:
        """Stop streaming and wait for the task to unwind."""
        if self._task is None:
            return
        task, self._task = self._task, None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        period = FRAME_MS / 1000
        next_at = loop.time()
        sample = 0
        while True:
            self._sink(slin_frame(sample, frequency_hz=self._frequency_hz))
            self.frames_sent += 1
            sample += SAMPLES_PER_FRAME
            next_at += period
            await asyncio.sleep(max(0.0, next_at - loop.time()))


class WavTee:
    """An audio sink that records the fake's frames to a WAV file.

    Until the Console can play Listen-in, this is how a human checks the
    synthetic audio is audio: run the harness with ``--fake-audio-wav`` and play
    the file. One file spans the whole run; the silence between calls is elided,
    so successive tones butt up against each other.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE_HZ)

    def __call__(self, frame: bytes) -> None:
        self._wav.writeframes(frame)

    def close(self) -> None:
        self._wav.close()


def write_wav(path: Path, frames: Iterable[bytes]) -> None:
    """Write slin16 frames to a mono 8 kHz WAV file."""
    tee = WavTee(path)
    try:
        for frame in frames:
            tee(frame)
    finally:
        tee.close()
