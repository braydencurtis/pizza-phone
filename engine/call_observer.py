"""The engine's :class:`~core.observer.CallObserver`: the worker thread's way home.

``core.flow`` runs in a worker thread (``asyncio.to_thread``) and notifies its
observer from there; the Console reads the live Call Session on the event loop.
This class is the crossing between the two, and it is the mirror image of the
hop ``ARICallIO`` makes in the other direction — that one submits a coroutine to
the loop and *blocks* the worker on the result, because the caller is waiting on
a prompt to finish. This one hands the loop a closure and returns immediately,
because nobody is waiting on a cockpit update and a caller mid-attempt certainly
should not be.

**The invariant is that the session is only ever mutated on the event loop.**
The worker thread never touches it. Every other write to a ``CallSession`` —
the digits from ``ChannelDtmfReceived``, the state changes, ``complete()`` — is
already made on the loop, so keeping these there too means the whole live state
is single-threaded and the Console can never build a snapshot from a session
caught half-written: one moment's attempt count beside another moment's node.
Mutating from the worker thread would be the phase's one genuine race.

Two failures are absorbed rather than raised, because these calls sit directly
in the path of a live caller's attempt and telemetry is never worth a call:
a loop that has already closed (shutdown races the last emission), and a change
listener that throws (a browser socket that died).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from engine.call_session import CallSession

logger = logging.getLogger(__name__)


class EngineCallObserver:
    """Carries ``core.flow``'s progress from the worker thread to the live session.

    An observer belongs to **one** Call Session and will write to no other.
    That is the point of holding both ``session`` (the call this observer was
    made for) and ``active_session`` (whoever the engine holds now): the flow
    runs in a thread the engine does not join, and ``_handle_call``'s
    ``finally:`` frees the slot without waiting for it. So a caller who hangs up
    while the flow sits in a thirty-second ``read_dtmf`` leaves that thread
    alive and still emitting, and by the time the loop runs the closure the
    booth may have a different caller on the line. Writing then would put one
    caller's attempt count on another caller's panel — so an emission lands only
    while the call that produced it is still the one being shown.

    This is the same identity check ``CallEngine._clear`` makes before dropping
    a finished call, and the same one ``_on_dtmf`` makes on the channel id.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        session: CallSession,
        active_session: Callable[[], CallSession | None],
        on_change: Callable[[], None],
    ) -> None:
        self._loop = loop
        self._session = session
        self._active_session = active_session
        self._on_change = on_change

    # -- the CallObserver seam (called on the worker thread) ---------------

    def attempt_started(self, attempt: int, limit: int) -> None:
        self._apply(lambda session: session.begin_attempt(attempt, limit))

    def node_entered(self, index: int, depth: int, terminal: bool) -> None:
        self._apply(lambda session: session.enter_node(index, depth, terminal))

    def puzzle_selected(self, puzzle_id: str) -> None:
        self._apply(lambda session: session.select_puzzle(puzzle_id))

    # -- the crossing ------------------------------------------------------

    def _apply(self, change: Callable[[CallSession], None]) -> None:
        """Hand the loop a write to make, and get straight back to the call."""
        try:
            self._loop.call_soon_threadsafe(self._on_loop, change)
        except RuntimeError:
            # The loop is closed — the engine is shutting down under a call
            # still in flight. There is no cockpit left to update.
            logger.debug("Dropped a call observation: the loop has gone", exc_info=True)

    def _on_loop(self, change: Callable[[CallSession], None]) -> None:
        session = self._session
        if self._active_session() is not session or session.is_over:
            return
        change(session)
        try:
            self._on_change()
        except Exception:
            logger.exception("A change listener failed; continuing the call")
