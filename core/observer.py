"""The second seam out of `core/`: telling the Operator what the caller is doing.

``CallIO`` is how the game logic talks to the *caller* — play this, read that,
hang up. This is how it talks to the *Operator*, and the two are kept apart on
purpose (ADR-0003): ``CallIO``'s smallness is the reason the same flow logic ran
unchanged under both the AGI driver and the ARI Call Engine, and widening it
with a ``progress()`` method would put two audiences on one seam.

The seam exists because the flow functions return exactly once, at the terminal
outcome. Everything the cockpit wants to show *during* a call — which attempt
the caller is on, which room of the maze they are in, which riddle they were
given — is computed inside those functions and, without this, never escapes.

Three things are worth knowing about implementing one:

**It is called from the worker thread.** ``core.flow`` is synchronous and the
engine runs it in a thread; an implementation that touches event-loop state must
marshal onto the loop itself (``loop.call_soon_threadsafe``), the mirror image
of the hop ``ARICallIO`` makes in the other direction. See
``engine/call_observer.py``.

**It must not raise, and must not block.** These calls sit directly in the path
of a live call: an observer that throws would fail the caller's attempt, and one
that blocks would stall the game while somebody is on the phone. Telemetry is
never worth a call.

**It is optional.** Every flow function defaults to :data:`NULL_OBSERVER`, so
`core/` stays usable — and testable — with no observer at all.
"""

from __future__ import annotations

from typing import Protocol


class CallObserver(Protocol):
    """Game-significant moments inside a Call Session, as they happen."""

    def attempt_started(self, attempt: int, limit: int) -> None:
        """The caller is about to be asked for their ``attempt``-th answer.

        Announced *before* the read, not after the verdict: the point is to show
        how close the caller is to Exile while they are dialling, so a cockpit
        that only learned of an attempt once it had been judged would always be
        one behind. ``limit`` is the Attempt Limit from this call's Config
        Snapshot — not necessarily what Global Config says now.
        """
        ...

    def node_entered(self, index: int, depth: int, terminal: bool) -> None:
        """The caller has arrived at a room of the Roguelike Phone-Tree.

        ``depth`` is how many rooms they have walked through, which is the part
        an Operator can actually read — the tree is regenerated per Call Session,
        so ``index`` points into a map only this call has. ``terminal`` marks the
        leaf, where the Code is read aloud: the one position in the maze worth
        spotting from across a room.
        """
        ...

    def puzzle_selected(self, puzzle_id: str) -> None:
        """This Call Session drew ``puzzle_id`` from the Puzzle Pool.

        Which riddle the caller is hearing, so the Operator knows what they are
        being asked rather than only whether they got it right.
        """
        ...


class _NullObserver:
    """The observer used when nobody is watching. Every method does nothing."""

    def attempt_started(self, attempt: int, limit: int) -> None:
        return None

    def node_entered(self, index: int, depth: int, terminal: bool) -> None:
        return None

    def puzzle_selected(self, puzzle_id: str) -> None:
        return None


# The default for every flow function. A shared do-nothing instance rather than
# ``None`` so the flow code can just call the seam, with no null check at each
# of the four emission sites to forget at the fifth.
NULL_OBSERVER: CallObserver = _NullObserver()
