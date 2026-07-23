from __future__ import annotations

from typing import Any, Protocol


class RoguelikeContext(Protocol):
    def speak(self, text: str) -> None: ...
    def read_choice(self, keys: str) -> str: ...


Node = dict[str, Any]


def make_tree() -> list[Node]:
    return [
        {
            "text": "You wake on a damp concrete floor. Fluorescent lights hum overhead. Press 1 to walk down the east corridor. Press 2 to crawl through the vent.",
            "choices": {"1": 1, "2": 2},
        },
        {
            "text": "The east corridor stretches into yellowed drywall. A door at the end is ajar. Press 1 to enter. Press 2 to double back.",
            "choices": {"1": 3, "2": 0},
        },
        {
            "text": "The vent drops you into a supply closet. Something scuttles in the dark. Press 1 to follow the noise. Press 2 to stay still.",
            "choices": {"1": 3, "2": 4},
        },
        {
            "text": "A phone sits on a desk. It rings once. Press 1 to answer. Press 2 to leave it.",
            "choices": {"1": 5, "2": 4},
        },
        {
            "text": "The room goes quiet. Too quiet. The lights flicker. Press 1 to run. Press 2 to search the room.",
            "choices": {"1": 0, "2": 5},
        },
        {
            "text": "A voice on the other end reads four digits. Listen carefully.",
        },
    ]


def handle(ctx: RoguelikeContext, code: str) -> list[str]:
    tree = make_tree()
    path: list[str] = []
    idx = 0

    while True:
        node = tree[idx]
        ctx.speak(node["text"])

        if "choices" not in node:
            ctx.speak(f"The code is {code}. Hang up and dial it now.")
            return path

        choice = ctx.read_choice(str(list(node["choices"].keys())))
        if choice not in node["choices"]:
            continue

        path.append(choice)
        idx = node["choices"][choice]
