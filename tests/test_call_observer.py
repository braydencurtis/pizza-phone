"""Tests for the engine's CallObserver implementation (ticket #37).

`core.flow` runs in a worker thread and notifies its observer from there, while
the Console reads the live session on the event loop. Everything interesting
about `EngineCallObserver` is that boundary: it is the mirror image of the hop
`ARICallIO` makes in the other direction, and getting it wrong is the primary
race risk of Phase 2.

The invariant these tests defend is **the session is only ever mutated on the
event loop**. The worker thread hands the loop a closure and returns; it never
touches the session itself. Without that, a snapshot built on the loop could
catch the session half-written — an attempt count from one moment beside a node
from another.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

from engine.call_observer import EngineCallObserver
from engine.call_session import CallSession, MazePosition


def _session() -> CallSession:
    return CallSession(
        session_id="sess-1",
        channel_id="chan-1",
        started_at=datetime(2026, 9, 1, 20, 0, tzinfo=UTC),
        state="in_mode",
    )


def test_an_attempt_reaches_the_session() -> None:
    async def run() -> None:
        session = _session()
        changes = 0

        def on_change() -> None:
            nonlocal changes
            changes += 1

        observer = EngineCallObserver(
            asyncio.get_running_loop(), session, lambda: session, on_change
        )
        await asyncio.to_thread(observer.attempt_started, 2, 3)
        await asyncio.sleep(0)

        assert session.current_attempt == 2
        assert session.attempt_limit == 3
        assert changes == 1

    asyncio.run(run())


def test_a_node_and_a_puzzle_reach_the_session() -> None:
    async def run() -> None:
        session = _session()
        observer = EngineCallObserver(
            asyncio.get_running_loop(), session, lambda: session, lambda: None
        )

        await asyncio.to_thread(observer.puzzle_selected, "riddle-07.wav")
        await asyncio.to_thread(observer.node_entered, 4, 2, False)
        await asyncio.sleep(0)

        assert session.puzzle_id == "riddle-07.wav"
        assert session.node == MazePosition(index=4, depth=2, terminal=False)

        await asyncio.to_thread(observer.node_entered, 5, 3, True)
        await asyncio.sleep(0)
        assert session.node == MazePosition(index=5, depth=3, terminal=True)

    asyncio.run(run())


def test_the_worker_thread_never_touches_the_session_itself() -> None:
    """The invariant: all mutation happens on the loop, single-threaded.

    A session mutated from the worker thread while the Console reads it on the
    loop is how a cockpit ends up showing one call's attempt beside another
    call's node. The observer's job is to make that impossible by construction,
    so it is asserted directly rather than hoped for.
    """

    async def run() -> None:
        session = _session()
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        mutating_threads: list[int] = []

        class WatchedSession(CallSession):
            def __setattr__(self, name: str, value: object) -> None:
                mutating_threads.append(threading.get_ident())
                super().__setattr__(name, value)

        watched = WatchedSession(
            session_id="sess-1",
            channel_id="chan-1",
            started_at=session.started_at,
            state="in_mode",
        )
        observer = EngineCallObserver(loop, watched, lambda: watched, lambda: None)

        mutating_threads.clear()
        worker_thread = await asyncio.to_thread(_emit_from_thread, observer)
        await asyncio.sleep(0)

        assert worker_thread != loop_thread, "the emission must come off the loop"
        assert mutating_threads, "the emission should have reached the session"
        assert set(mutating_threads) == {loop_thread}

    asyncio.run(run())


def _emit_from_thread(observer: EngineCallObserver) -> int:
    observer.attempt_started(1, 3)
    observer.node_entered(0, 0, False)
    observer.puzzle_selected("p.wav")
    return threading.get_ident()


def test_emissions_for_a_call_that_is_no_longer_live_are_dropped() -> None:
    """A worker thread outliving its call must write nowhere.

    The flow runs in a thread the engine does not join, and `_handle_call`'s
    `finally:` frees the slot without waiting for it. So a call torn down by an
    exception — or a caller who hung up while the flow sat in a 30-second
    `read_dtmf` — leaves that thread alive and still emitting.
    """

    async def run() -> None:
        ended = _session()
        live: CallSession | None = ended
        observer = EngineCallObserver(
            asyncio.get_running_loop(), ended, lambda: live, lambda: None
        )

        live = None
        await asyncio.to_thread(observer.attempt_started, 3, 3)
        await asyncio.sleep(0)
        assert ended.current_attempt is None

    asyncio.run(run())


def test_a_straggler_thread_cannot_write_to_the_next_caller() -> None:
    """The one that matters: call A's thread must never touch call B's panel.

    Caller A hangs up mid-attempt; the engine tears the call down and frees the
    slot while A's flow thread is still blocked in `read_dtmf`. Caller B picks
    up. A's thread then times out, loops round, and announces its next attempt.
    Without an identity check that lands on B — whose panel would show them
    three attempts deep into a game they just started.
    """

    async def run() -> None:
        first = _session()
        successor = _session()
        successor.session_id = "sess-2"
        live: CallSession | None = first

        observer = EngineCallObserver(
            asyncio.get_running_loop(), first, lambda: live, lambda: None
        )

        live = successor
        await asyncio.to_thread(observer.attempt_started, 3, 3)
        await asyncio.to_thread(observer.node_entered, 4, 2, False)
        await asyncio.to_thread(observer.puzzle_selected, "riddle-07.wav")
        await asyncio.sleep(0)

        assert successor.current_attempt is None
        assert successor.node is None
        assert successor.puzzle_id is None
        assert first.current_attempt is None, "and not to the dead call either"

    asyncio.run(run())


def test_an_observer_whose_loop_has_gone_does_not_raise() -> None:
    """Telemetry is never worth a call.

    These calls sit directly in the path of a live caller's attempt, so an
    observer that raised would fail the attempt over a cockpit update.
    """
    loop = asyncio.new_event_loop()
    session = _session()
    observer = EngineCallObserver(loop, session, lambda: session, lambda: None)
    loop.close()

    observer.attempt_started(1, 3)
    observer.node_entered(0, 0, False)
    observer.puzzle_selected("p.wav")
    assert session.current_attempt is None


def test_a_change_listener_that_raises_does_not_reach_the_caller() -> None:
    async def run() -> None:
        session = _session()

        def explode() -> None:
            raise RuntimeError("a browser socket died")

        observer = EngineCallObserver(
            asyncio.get_running_loop(), session, lambda: session, explode
        )
        await asyncio.to_thread(observer.attempt_started, 1, 3)
        await asyncio.sleep(0)
        # The state still landed; only the announcement failed.
        assert session.current_attempt == 1

    asyncio.run(run())
