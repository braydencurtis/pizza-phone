from __future__ import annotations

import random
from typing import Literal

from core import mode_roguelike


class HeadlessRoguelikeContext:
    """In-memory RoguelikeContext that auto-picks a random choice at each node."""

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


def run_roguelike(
    code: str,
    seed: int | None = None,
    strategy: Literal["random", "first"] = "random",
) -> dict[str, object]:
    """Run the roguelike mode headlessly, walking the tree to its terminal node."""
    if seed is not None:
        random.seed(seed)
    ctx = HeadlessRoguelikeContext(strategy=strategy)
    result = mode_roguelike.handle(ctx, code, seed=seed)
    path = result["path"]
    return {
        # Deliberately NOT ``result["outcome"]``, and that is a trap rather than
        # a decision. Since #59 a simulated walk can be Exiled, and reporting
        # that honestly would be worse than this lie: the only caller of this
        # function is ``Router.dispatch``, which runs it *instead of* looking at
        # the real caller's walk, so an honest "exile" here would file a live
        # caller's win as a loss about one time in four. The lie is inert only
        # because ``flow.run_roguelike`` intercepts a real Exile before
        # dispatching. #56 removes the simulation, and this with it.
        "outcome": "succeed" if path else "fail",
        "path": path,
        "nodes_visited": result["nodes_visited"],
        "attempts": len(path),
        "spoken": ctx.spoken,
    }
