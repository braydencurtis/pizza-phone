"""Adapts the async :class:`~engine.ari_client.ARIClient` to the synchronous
``core.CallIO`` protocol — the ARI implementation of the seam the retired AGI
driver filled for AGI. This is where the sync→async shift is absorbed.

The bridge: ``core.flow`` is synchronous, ARI is event-driven, so the engine
runs each mode handler in a worker thread and every method here submits its ARI
coroutine back to the loop via :meth:`ARICallIO._run_on_loop`, blocking the
worker on the result. See engine/README.md ("The ARI CallIO adapter") for the
full rationale. Consequence for callers: invoke these methods from a thread
*other* than the one running ``loop`` — calling from the loop thread would
deadlock, since the submitted coroutine could never run.

``media`` arguments are ARI resource URIs (``sound:``/``recording:`` …); core
replays whatever the caller passed, so naming stays the driver's concern.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

from core.tts import TTSBackend, detect_backend, synthesize
from engine.ari_client import ARIClient

# Dialplan context the caller is routed into on success (rings the Upstairs
# Phone via Dial(PJSIP/${UPSTREAM_EXT}) — see asterisk/extensions.conf).
SUCCESS_CONTEXT = "pizza-success"
SUCCESS_EXTENSION = "s"
SUCCESS_PRIORITY = 1

T = TypeVar("T")


class ARICallIO:
    """``CallIO`` backed by ARI, bridging the sync flow to the async client.

    ``loop`` is the engine's running event loop; ``channel_id`` scopes every
    command to this one call.
    ``tts``/``output_dir`` follow ``core.tts`` defaults (auto-detected backend,
    ``/tmp`` output) when omitted.
    """

    def __init__(
        self,
        ari: ARIClient,
        channel_id: str,
        loop: asyncio.AbstractEventLoop,
        upstream_ext: str,
        *,
        tts: TTSBackend | None = None,
        output_dir: Path | None = None,
    ) -> None:
        self._ari = ari
        self._channel_id = channel_id
        self._loop = loop
        self._upstream_ext = upstream_ext
        self._tts = tts
        self._output_dir = output_dir

    def play(self, media: str) -> None:
        self._run_on_loop(self._ari.play(self._channel_id, media))

    def read_dtmf(self, num_digits: int, timeout_ms: int) -> str:
        return self._run_on_loop(
            self._ari.read_digits(
                self._channel_id,
                num_digits=num_digits,
                inter_digit_timeout_ms=timeout_ms,
            )
        )

    def speak(self, text: str) -> None:
        # Synthesis is a blocking subprocess; running it inline is fine — this
        # method already executes in the worker thread, off the event loop.
        if self._tts is None:
            self._tts = detect_backend()()
        audio_path = synthesize(text, backend=self._tts, output_dir=self._output_dir)
        self._run_on_loop(self._ari.play(self._channel_id, sound_uri(audio_path)))

    def hangup(self) -> None:
        self._run_on_loop(self._ari.hangup(self._channel_id))

    def to_success(self) -> None:
        # Set UPSTREAM_EXT before leaving Stasis: the pizza-success context
        # dials PJSIP/${UPSTREAM_EXT} to ring the Upstairs Phone.
        self._run_on_loop(self._ari.set_channel_var(self._channel_id, "UPSTREAM_EXT", self._upstream_ext))
        self._run_on_loop(
            self._ari.continue_in_dialplan(
                self._channel_id,
                context=SUCCESS_CONTEXT,
                extension=SUCCESS_EXTENSION,
                priority=SUCCESS_PRIORITY,
            )
        )

    # -- internals ---------------------------------------------------------

    def _run_on_loop(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run ``coro`` on the engine's loop and block this thread on its result.

        The cross-thread hop is the whole point: the caller is a worker thread,
        so submitting here keeps the loop free to service the very events
        (``PlaybackFinished``, DTMF) the coroutine is waiting on.
        """
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


def sound_uri(audio_path: Path) -> str:
    """An ARI ``sound:`` URI for a WAV path (Asterisk wants no extension).

    Shared by the adapter's ``speak`` and the engine's puzzle-prompt dispatch —
    both name a local WAV to Asterisk the same way.
    """
    return f"sound:{audio_path.with_suffix('')}"
