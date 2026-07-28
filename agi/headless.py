from __future__ import annotations

import random
from typing import Literal

from agi import mode_roguelike


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
        "outcome": "succeed" if path else "fail",
        "path": path,
        "nodes_visited": result["nodes_visited"],
        "attempts": len(path),
        "spoken": ctx.spoken,
    }
