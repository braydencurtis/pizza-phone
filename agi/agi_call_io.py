"""Adapts an :class:`AGIChannel` to the channel-agnostic ``CallIO`` protocol.

This is the AGI-specific half of the seam: it maps the generic ``CallIO`` calls
that ``core.flow`` makes onto concrete Asterisk AGI verbs. The ARI Call Engine
will supply its own ``CallIO`` implementation; the flow logic stays shared.
"""

from __future__ import annotations

from agi.agi_channel import AGIChannel
from core.tts import TTSBackend, detect_backend, synthesize

# Dialplan context the caller is routed into on success (rings the Upstairs Phone).
SUCCESS_TARGET = "pizza-success,s,1"
# Silent prompt file used when reading DTMF (Asterisk streams it, then listens).
DTMF_PROMPT = "silence/beam"


class AGICallIO:
    """`CallIO` backed by Asterisk AGI verbs over an :class:`AGIChannel`."""

    def __init__(self, channel: AGIChannel, upstream_ext: str, tts: TTSBackend | None = None) -> None:
        self.channel = channel
        self.upstream_ext = upstream_ext
        self._tts = tts

    def play(self, media: str) -> None:
        self.channel.stream_file(media)

    def read_dtmf(self, num_digits: int, timeout_ms: int) -> str:
        return self.channel.read_digits(
            filename=DTMF_PROMPT,
            num_digits=num_digits,
            timeout=timeout_ms,
        )

    def speak(self, text: str) -> None:
        if self._tts is None:
            self._tts = detect_backend()()
        audio_path = synthesize(text, backend=self._tts)
        self.channel.verbose(f"TTS: {audio_path}")
        self.channel.exec_app("Playback", str(audio_path))

    def hangup(self) -> None:
        self.channel.hangup()

    def to_success(self) -> None:
        self.channel.set_variable("UPSTREAM_EXT", self.upstream_ext)
        self.channel.exec_app("Goto", SUCCESS_TARGET)
