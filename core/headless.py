"""Walk the Roguelike Phone-Tree with nobody on the phone.

A simulator, and **only** a simulator: it plays the maze against itself so the
tree and its bound can be measured in bulk, the way CONTEXT.md's *Walking the
maze out* row was measured. Nothing in a live call path may reach it — a real
caller's Walk comes back from ``mode_roguelike.handle`` through their own
``CallIO``, and is what ``core.flow`` records.

That rule is written here because it was broken here. Until #56 ``Router``
called this on every roguelike call, so the maze the history described was one
this module had just walked at random on a tree it had just built, while the
caller's real walk was thrown away. A won call was filed under a stranger's
rooms, and filed as won whatever the caller had done, because a simulated
walker with unlimited patience always reaches a leaf.
"""

from __future__ import annotations

import random
from typing import Literal

from core import mode_roguelike


class HeadlessRoguelikeContext:
    """In-memory RoguelikeContext that auto-picks a choice at each node.

    ``random`` is a caller pressing keys at random; ``first`` is one mashing the
    same key every time, which is the commoner shape and the one that mostly
    walks the bound out.
    """

    def __init__(self, strategy: Literal["random", "first"] = "random") -> None:
        self.spoken: list[str] = []
        self.strategy = strategy

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def read_choice(self, keys: str) -> str:
        digits = [ch for ch in keys if ch.isdigit()]
        if not digits:
            return "1"
        if self.strategy == "first":
            return digits[0]
        return random.choice(digits)


class SimulatedWalk(mode_roguelike.Walk):
    """A :class:`~core.mode_roguelike.Walk` plus what the maze said while walking it."""

    attempts: int
    spoken: list[str]


def run_roguelike(
    code: str,
    seed: int | None = None,
    strategy: Literal["random", "first"] = "random",
) -> SimulatedWalk:
    """Simulate one walk of the maze, to whichever of its endings it reaches.

    The outcome is the walker's own — ``succeed`` at the room holding the Code,
    ``exile`` at the Walk Bound — because a simulation that could not report a
    loss could not measure how hard the maze is, which is the only thing this is
    for. It used to report ``succeed`` for both, and that was a lie kept alive
    by its one caller being ``Router.dispatch``, where an honest ``exile`` would
    have filed a live caller's win as a loss (#56).

    ``hangup`` cannot arise: this context always answers with a key the room
    offers, so nobody ever goes quiet on it.
    """
    if seed is not None:
        random.seed(seed)
    ctx = HeadlessRoguelikeContext(strategy=strategy)
    walk = mode_roguelike.handle(ctx, code, seed=seed)
    return SimulatedWalk(
        outcome=walk["outcome"],
        path=walk["path"],
        nodes_visited=walk["nodes_visited"],
        attempts=mode_roguelike.moves_made(walk),
        spoken=ctx.spoken,
    )
