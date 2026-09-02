"""The Call Engine: one asyncio process that owns live calls via ARI/Stasis.

This is the Phase 1 skeleton (issue #19). It connects to Asterisk over ARI,
and on each ``StasisStart`` runs one Call Session end-to-end: take a Config
Snapshot, answer, dispatch to the mode handler (tweeted / puzzle / roguelike)
through the ``ARICallIO`` seam, then persist the completed session to the SQLite
``CallStore``. The snapshot is taken once, at pickup, and the whole call is
judged against it — see ``core/config.py``. The mode handlers and the
success/hangup routing are ``core.flow`` unchanged — the same logic the retired
AGI driver ran.

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
reads; the Console server (``engine/console.py``) slots into this same process
and reads it directly. ``on_change`` is the other half: the engine announces
that the live state moved, and the Console turns each announcement into a
whole-state snapshot broadcast to every open browser. The engine knows nothing
about HTTP, sockets or snapshots — only that somebody wants to be told. See
engine/README.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core import flow
from core.config import CONFIG_FILENAME, take_snapshot
from core.mode_puzzle import PuzzleSelector
from core.router import Router
from core.tts import TTSBackend
from engine.ari_call_io import ARICallIO, sound_uri
from engine.ari_client import (
    CHANNEL_DTMF_RECEIVED,
    CHANNEL_HANGUP_REQUEST,
    STASIS_END,
    STASIS_START,
    ARIClient,
)
from engine.call_observer import EngineCallObserver
from engine.call_session import CallSession
from engine.call_store import CallStore, new_session_id

logger = logging.getLogger(__name__)

# ARI media URIs for caller feedback (the ARI analogues of the AGI driver's
# builtin-prompt names). Wrong-answer beep and the Exile disconnect prompt.
EXILE_MEDIA = "sound:voicemail/busy"
WRONG_MEDIA = "sound:beep"

# How long a finished Call Session stays on the Console before the booth reads
# idle again (#36). Terminal states are the point of the panel — a win that
# flickered past for a millisecond would be a win the Operator never saw — and
# the snapshot a browser receives is built when the broadcast runs, so without a
# pause the terminal state could be overwritten by the idle one before either is
# sent. Display only: the slot is free the instant the call ends.
AFTERGLOW_S = 6.0


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
        afterglow_s: float = AFTERGLOW_S,
    ) -> None:
        self._ari = ari
        self._store = store
        self._config_dir = config_dir
        self._log_dir = log_dir
        self._audio_dir = audio_dir
        self._tts = tts
        self._afterglow_s = afterglow_s

        # Shared in-memory state: the call the Console is showing — the live
        # one, or the one that just ended, for as long as the afterglow lasts.
        # None only when there is nothing to show.
        self.active_session: CallSession | None = None
        self._change_callbacks: list[Callable[[], None]] = []

        self._loop: asyncio.AbstractEventLoop | None = None
        self._call_task: asyncio.Task[None] | None = None
        self._afterglow_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Initialize the store, register the handler, and connect to ARI."""
        self._loop = asyncio.get_running_loop()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        await self._store.initialize()
        self._ari.on(STASIS_START, self._on_stasis_start)
        self._ari.on(CHANNEL_DTMF_RECEIVED, self._on_dtmf)
        for event_type in (STASIS_END, CHANNEL_HANGUP_REQUEST):
            self._ari.on(event_type, self._on_channel_gone)
        await self._ari.connect()
        logger.info("Call engine started")

    async def aclose(self) -> None:
        """Let the active call finish, then close the ARI connection.

        The afterglow is abandoned rather than waited out: nobody is left to
        read a finished call off a console whose engine is shutting down.
        """
        await self.wait_for_idle()
        if self._afterglow_task is not None:
            self._afterglow_task.cancel()
            self._afterglow_task = None
        await self._ari.close()

    async def wait_for_idle(self) -> None:
        """Await the in-flight call, if any. Used by shutdown and tests."""
        if self._call_task is not None:
            await asyncio.shield(self._call_task)

    # -- the console's view ------------------------------------------------

    def on_change(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Be told whenever the live state moves. Returns an unsubscribe.

        Deliberately a bare nudge with no payload: the Console re-reads
        ``active_session`` and sends a whole-state snapshot, so there is no
        event shape to keep in step with the wire format, and a listener that
        misses one is corrected by the next.
        """
        self._change_callbacks.append(callback)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._change_callbacks.remove(callback)

        return unsubscribe

    def _notify_change(self) -> None:
        """Announce that the live state moved.

        Every listener is called even if an earlier one raises, and a raising
        listener is only logged: a browser socket that died must not cost the
        caller on the line their call.
        """
        for callback in list(self._change_callbacks):
            try:
                callback()
            except Exception:
                logger.exception("A change listener failed; continuing the call")

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

        live = self.active_session
        if live is not None and not live.is_over:
            logger.warning(
                "Busy with session %s; hanging up extra channel %s",
                live.session_id,
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
        self._notify_change()
        self._call_task = asyncio.create_task(
            self._handle_call(session), name=f"call-{session.session_id}"
        )

    async def _on_dtmf(self, event: dict[str, Any]) -> None:
        """A digit was dialled: show it on the Console as the caller presses it.

        Read-only observation of an event the engine already receives — the
        digits are still collected, and judged, by ``ARICallIO``/``core.flow``.
        Digits for any channel but the live one are ignored: a stray event from
        a call that has ended has no business writing to the cockpit.
        """
        session = self.active_session
        if session is None or session.is_over:
            return
        channel_id = event.get("channel", {}).get("id", "")
        digit = event.get("digit", "")
        if not digit or channel_id != session.channel_id:
            return
        session.record_digit(digit)
        self._notify_change()

    async def _on_channel_gone(self, event: dict[str, Any]) -> None:
        """The caller's channel left us — note who ended the call.

        Whatever the mode handler is in the middle of will now fail: every ARI
        command against a dead channel 404s. Recording the hangup *here*, while
        we still know it was the caller, is what lets the terminal state say
        "hung up" instead of blaming the engine for the exception that follows.
        """
        session = self.active_session
        if session is None or session.is_over:
            return
        if event.get("channel", {}).get("id", "") != session.channel_id:
            return
        session.caller_gone = True

    async def _handle_call(self, session: CallSession) -> None:
        """Answer, run the mode to a terminal outcome, and persist the session.

        The Config Snapshot is taken first — before the channel is even
        answered — so an Operator rotating the Code or switching Mode mid-call
        lands on the next caller, never this one (#34). The mode handler is synchronous ``core.flow`` run in a worker
        thread; it does the success/hangup routing itself through ``ARICallIO``.
        Any failure is contained here so one bad call can't take down the engine
        — the channel is torn down and the slot freed regardless.

        Both ways out lead to the same two closing steps, which is why they sit
        in the ``finally``: every call that ended is written to the store, and
        then announced to the Console. The handler returning is only the tidy
        way to end — a caller who hangs up mid-playback ends the call by making
        every following ARI command 404, and that arrives here as an exception
        (#50). Persisting on the success path alone left those calls in the
        cockpit and out of the history.
        """
        try:
            session.config = await asyncio.to_thread(
                take_snapshot, self._config_dir / CONFIG_FILENAME
            )
            session.mode = session.config.mode
            # The Mode is only known once the snapshot is taken, and it is the
            # first thing the Operator wants to see about a new call.
            self._notify_change()
            await self._ari.answer(session.channel_id)
            session.enter_mode()
            self._notify_change()
            result = await asyncio.to_thread(self._run_mode, session)
            session.complete(result)
        except Exception:
            logger.exception("Session %s failed", session.session_id)
            session.abandon()
            await self._safe_hangup(session.channel_id)
        finally:
            await self._persist(session)
            self._announce_end_and_linger(session)

    async def _persist(self, session: CallSession) -> None:
        """Write the finished call to the store, however it finished.

        The outcome is either the mode handler's own or the one
        :meth:`CallSession.abandon` synthesised when the handler never returned
        — and the second is not the rare case: a caller putting the handset
        down mid-prompt is one of the commonest endings there is.

        Two sessions are not written. One with no Mode never had a Config
        Snapshot, so it never had a game the caller can be recorded as having
        played; one with no outcome never reached an ending at all (a shutdown
        cancelling the call task mid-flight). Both are engine-log material only.

        A store failure is logged and swallowed. The call is over and the
        channel is down by now, so raising would turn one lost row into an
        unhandled task exception and cost the Console its terminal state as
        well.
        """
        if session.mode is None or session.outcome is None:
            logger.warning(
                "Session %s left no record: mode=%r outcome=%r",
                session.session_id,
                session.mode,
                session.outcome,
            )
            return
        try:
            await self._store.add(session.to_record())
        except Exception:
            logger.exception("Could not persist session %s", session.session_id)
            return
        logger.info(
            "Session %s ended: mode=%s outcome=%s attempts=%d",
            session.session_id,
            session.mode,
            session.outcome,
            session.attempts,
        )

    # -- the afterglow -----------------------------------------------------

    def _announce_end_and_linger(self, session: CallSession) -> None:
        """Announce how the call ended, then hold it in view for a moment.

        Two announcements, not one: the terminal state — Handed Off, Exiled,
        hung up — is the thing the Operator is watching for, so it is broadcast
        on its own before the booth reads idle. The slot is already free by
        then; the afterglow is what the Console shows, never what the engine
        will accept.
        """
        self._notify_change()
        if self._afterglow_s <= 0:
            self._clear(session)
            return
        self._afterglow_task = asyncio.create_task(
            self._afterglow(session), name=f"afterglow-{session.session_id}"
        )

    async def _afterglow(self, session: CallSession) -> None:
        await asyncio.sleep(self._afterglow_s)
        self._clear(session)

    def _clear(self, session: CallSession) -> None:
        """Drop a finished call from view — unless a new caller already took its place."""
        if self.active_session is not session:
            return
        self.active_session = None
        self._notify_change()

    # -- mode dispatch (runs in the worker thread) -------------------------

    def _run_mode(self, session: CallSession) -> dict[str, Any]:
        """Run the session's mode through ``core.flow``, against its snapshot.

        Config is not read here: the session already carries the snapshot taken
        at pickup, and the ``Router`` built from it is what every attempt of
        this call is judged against. Runs off the event loop (worker thread)
        because ``core.flow`` is blocking and every ``ARICallIO`` call hops back
        to the loop. Same per-mode dispatch the retired AGI entry point ran,
        with ARI media names.

        Two seams go in here, pointing opposite ways: ``ARICallIO`` carries the
        game's words *to the caller*, and ``EngineCallObserver`` carries its
        progress *to the Operator* (#37). Keeping them apart is ADR-0003.
        """
        assert self._loop is not None  # set in start(), before any call runs
        config = session.config
        assert config is not None  # taken in _handle_call, before this runs
        router = Router(config, log_dir=self._log_dir)

        io = ARICallIO(
            self._ari,
            session.channel_id,
            self._loop,
            upstream_ext=config.upstream_extension,
            tts=self._tts,
        )
        # Bound to *this* session, and checked against the live one at write
        # time: this thread outlives a call the engine tore down, and by then
        # the slot may hold somebody else. See engine/call_observer.py.
        observer = EngineCallObserver(
            self._loop, session, lambda: self.active_session, self._notify_change
        )

        if config.mode == "tweeted":
            return flow.run_tweeted(
                io,
                router,
                exile_media=EXILE_MEDIA,
                wrong_media=WRONG_MEDIA,
                observer=observer,
            )
        if config.mode == "puzzle":
            return self._run_puzzle(io, router, observer)
        if config.mode == "roguelike":
            return flow.run_roguelike(io, router, observer=observer)
        raise ValueError(f"Unknown mode: {config.mode!r}")

    def _run_puzzle(
        self, io: ARICallIO, router: Router, observer: EngineCallObserver
    ) -> dict[str, Any]:
        """Pick a puzzle from the pool and run the puzzle flow.

        Selection is core; resolving the chosen WAV to an ARI ``sound:`` URI is
        engine-specific, so it stays here. Which puzzle was drawn is announced
        by the flow, not here, so the emission sits at the seam the tests drive.
        """
        puzzle_path = PuzzleSelector(self._audio_dir / "puzzles").pick()
        return flow.run_puzzle(
            io,
            router,
            puzzle_id=puzzle_path.name,
            prompt_media=sound_uri(puzzle_path),
            exile_media=EXILE_MEDIA,
            wrong_media=WRONG_MEDIA,
            observer=observer,
        )

    async def _safe_hangup(self, channel_id: str) -> None:
        """Best-effort teardown; the channel may already be gone."""
        try:
            await self._ari.hangup(channel_id)
        except Exception:
            logger.debug("Hangup of %s failed (already gone?)", channel_id, exc_info=True)
