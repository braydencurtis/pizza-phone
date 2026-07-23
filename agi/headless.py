from __future__ import annotations

from agi import mode_roguelike


class HeadlessRoguelikeContext:
    """In-memory RoguelikeContext that auto-picks the first choice at each node."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def read_choice(self, keys: str) -> str:
        for ch in keys:
            if ch.isdigit():
                return ch
        return "1"


def run_roguelike(code: str) -> dict[str, object]:
    """Run the roguelike mode headlessly, walking the tree to its terminal node."""
    ctx = HeadlessRoguelikeContext()
    path = mode_roguelike.handle(ctx, code)
    return {"outcome": "succeed" if path else "fail", "path": path, "attempts": len(path)}
