"""The Call Engine: one asyncio process that owns live calls via ARI/Stasis.

This is the Phase 1 skeleton (issue #19). It connects to Asterisk over ARI,
and on each ``StasisStart`` runs one Call Session end-to-end: answer, load
config, dispatch to the mode handler (tweeted / puzzle / roguelike) through the
``ARICallIO`` seam, then persist the completed session to the SQLite
``CallStore``. The mode handlers and the success/hangup routing are
``core.flow`` unchanged — the same logic the retired AGI driver ran.

**One call at a time.** The booth has a single phone, so the engine holds a
single :class:`~engine.call_session.CallSession` (``active_session``); a second
``StasisStart`` while one is live is hung up rather than queued.

**Why the handler doesn't block.** ``ARIClient`` dispatches event handlers
inline on its WebSocket reader task, so ``_on_stasis_start`` must return
promptly — if it awaited the whole call, the reader could never deliver the
DTMF and ``PlaybackFinished`` events the call depends on. It therefore spawns
the call as a background task, and that task runs the synchronous ``core.flow``
handler in a worker thread (``asyncio.to_thread``) while the loop stays free to
service events — the bridge ``ARICallIO`` is built around.

**Phase 2 seam.** ``active_session`` is the shared in-memory state the console
will read; the dashboard WS/HTTP server slots into this same process and reads
it directly. See engine/README.md.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core import flow
from core.mode_puzzle import PuzzleSelector
from core.router import Router
from core.tts import TTSBackend
from engine.ari_call_io import ARICallIO, sound_uri
from engine.ari_client import STASIS_START, ARIClient
from engine.call_session import CallSession
from engine.call_store import CallStore, new_session_id

logger = logging.getLogger(__name__)

# ARI media URIs for caller feedback (the ARI analogues of the AGI driver's
# builtin-prompt names). Wrong-answer beep and the Exile disconnect prompt.
EXILE_MEDIA = "sound:voicemail/busy"
WRONG_MEDIA = "sound:beep"


class CallEngine:
    """Owns the ARI event loop and drives one Call Session at a time.

    Construct with a connected-or-connectable :class:`ARIClient` and a
    :class:`CallStore`, plus the repo directories the modes need (``config``,
    ``logs``, ``audio``). Call :meth:`start` once to initialize the store and
    register the ``StasisStart`` handler, then keep the process alive; call
    :meth:`aclose` to drain the active call and disconnect.
    """

    def __init__(
        self,
        ari: ARIClient,
        store: CallStore,
        *,
        config_dir: Path,
        log_dir: Path,
        audio_dir: Path,
        tts: TTSBackend | None = None,
    ) -> None:
        self._ari = ari
        self._store = store
        self._config_dir = config_dir
        self._log_dir = log_dir
        self._audio_dir = audio_dir
        self._tts = tts

        # Shared in-memory state: the single live call, or None when idle. The
        # Phase 2 dashboard reads this off the engine to render the cockpit.
        self.active_session: CallSession | None = None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._call_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Initialize the store, register the handler, and connect to ARI."""
        self._loop = asyncio.get_running_loop()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        await self._store.initialize()
        self._ari.on(STASIS_START, self._on_stasis_start)
        await self._ari.connect()
        logger.info("Call engine started")

    async def aclose(self) -> None:
        """Let the active call finish, then close the ARI connection."""
        await self.wait_for_idle()
        await self._ari.close()

    async def wait_for_idle(self) -> None:
        """Await the in-flight call, if any. Used by shutdown and tests."""
        if self._call_task is not None:
            await asyncio.shield(self._call_task)

    # -- event handling ----------------------------------------------------

    async def _on_stasis_start(self, event: dict[str, Any]) -> None:
        """Accept a new call, or hang up if one is already live.

        Kept quick: it only claims the slot and spawns the call task, so the
        ARI reader stays free to deliver that call's events.
        """
        channel = event.get("channel", {})
        channel_id = channel.get("id", "")
        if not channel_id:
            logger.warning("StasisStart without a channel id: %r", event)
            return

        if self.active_session is not None:
            logger.warning(
                "Busy with session %s; hanging up extra channel %s",
                self.active_session.session_id,
                channel_id,
            )
            await self._ari.hangup(channel_id)
            return

        caller = channel.get("caller", {})
        session = CallSession(
            session_id=new_session_id(),
            channel_id=channel_id,
            started_at=datetime.now(UTC),
            caller_id=caller.get("number") or None,
        )
        self.active_session = session
        self._call_task = asyncio.create_task(
            self._handle_call(session), name=f"call-{session.session_id}"
        )

    async def _handle_call(self, session: CallSession) -> None:
        """Answer, run the mode to a terminal outcome, and persist the session.

        The mode handler is synchronous ``core.flow`` run in a worker thread;
        it does the success/hangup routing itself through ``ARICallIO``. Any
        failure is contained here so one bad call can't take down the engine —
        the channel is torn down and the slot freed regardless.
        """
        try:
            await self._ari.answer(session.channel_id)
            result = await asyncio.to_thread(self._run_mode, session)
            session.complete(result)
            await self._store.add(session.to_record())
            logger.info(
                "Session %s ended: mode=%s outcome=%s attempts=%d",
                session.session_id,
                session.mode,
                session.outcome,
                session.attempts,
            )
        except Exception:
            logger.exception("Session %s failed", session.session_id)
            await self._safe_hangup(session.channel_id)
        finally:
            self.active_session = None

    # -- mode dispatch (runs in the worker thread) -------------------------

    def _run_mode(self, session: CallSession) -> dict[str, Any]:
        """Load config and run the configured mode's ``core.flow`` handler.

        Runs off the event loop (worker thread) because ``core.flow`` is
        blocking and every ``ARICallIO`` call hops back to the loop. Mirrors
        ``agi/main.py``'s dispatch — same handlers, ARI media names.
        """
        assert self._loop is not None  # set in start(), before any call runs
        router = Router(config_dir=self._config_dir, log_dir=self._log_dir)
        config = router.load_config()
        mode = config.get("mode", "tweeted")
        code = config.get("code", "0000")
        max_attempts = config.get("attempt_limit", 3)
        upstream_ext = config.get("upstream_extension", "200")
        session.mode = mode

        io = ARICallIO(
            self._ari,
            session.channel_id,
            self._loop,
            upstream_ext=upstream_ext,
            tts=self._tts,
        )

        if mode == "tweeted":
            return flow.run_tweeted(
                io,
                router,
                code=code,
                max_attempts=max_attempts,
                exile_media=EXILE_MEDIA,
                wrong_media=WRONG_MEDIA,
            )
        if mode == "puzzle":
            return self._run_puzzle(io, router, code, max_attempts)
        if mode == "roguelike":
            return flow.run_roguelike(io, router, code=code)
        raise ValueError(f"Unknown mode: {mode!r}")

    def _run_puzzle(
        self, io: ARICallIO, router: Router, code: str, max_attempts: int
    ) -> dict[str, Any]:
        """Pick a puzzle from the pool and run the puzzle flow.

        Selection is core; resolving the chosen WAV to an ARI ``sound:`` URI is
        engine-specific, so it stays here (mirrors ``agi/main.py``).
        """
        puzzle_path = PuzzleSelector(self._audio_dir / "puzzles").pick()
        return flow.run_puzzle(
            io,
            router,
            code=code,
            max_attempts=max_attempts,
            puzzle_id=puzzle_path.name,
            prompt_media=sound_uri(puzzle_path),
            exile_media=EXILE_MEDIA,
            wrong_media=WRONG_MEDIA,
        )

    async def _safe_hangup(self, channel_id: str) -> None:
        """Best-effort teardown; the channel may already be gone."""
        try:
            await self._ari.hangup(channel_id)
        except Exception:
            logger.debug("Hangup of %s failed (already gone?)", channel_id, exc_info=True)
