from __future__ import annotations

import random
from typing import Protocol, TypedDict, TypeGuard, cast


class RoguelikeContext(Protocol):
    def speak(self, text: str) -> None: ...
    def read_choice(self, keys: str) -> str: ...


class ChoiceNode(TypedDict):
    text: str
    choices: dict[str, int]


class TerminalNode(TypedDict):
    text: str


Node = ChoiceNode | TerminalNode


def _is_terminal(node: Node) -> TypeGuard[TerminalNode]:
    return "choices" not in cast(dict, node)


def _build_forest(rng: random.Random, num_rooms: int = 5) -> list[str]:
    scenes: list[str] = [
        "You wake on a damp concrete floor. Fluorescent lights hum overhead.",
        "The east corridor stretches into yellowed drywall. A door at the end is ajar.",
        "The vent drops you into a supply closet. Something scuttles in the dark.",
        "A phone sits on a desk. It rings once.",
        "The room goes quiet. Too quiet. The lights flicker.",
        "A rusted door blocks the hallway. It's slightly ajar.",
        "Dripping water echoes in the tiled room. Footsteps fade behind you.",
        "A flickering exit sign points left. A shadow moves right.",
        "The walls are covered in faded maps. One room is circled in red.",
        "A generator buzzes in the corner. The air tastes like copper.",
        "Stacks of crates line the aisle. Something's written on the nearest one.",
        "A broken window lets in cold air. Scratches mark the windowsill.",
        "The corridor narrows to a squeeze. Your flashlight dies momentarily.",
        "An elevator shaft gapes open. The cables sway in the draft.",
        "A desk lamp illuminates a notepad with a sequence of numbers.",
        "Steam hisses from a pipe. The floor grating vibrates beneath you.",
        "A janitor's cart sits overturned. Bottles of cleaner line the shelves.",
        "The ceiling tiles sag with water damage. One pulls loose at your touch.",
        "A radio crackles with static. A voice cuts through briefly.",
        "Paint peels in strips like dead skin. The room smells of mildew.",
    ]
    return rng.sample(scenes, min(num_rooms, len(scenes)))


def make_tree(seed: int | None = None) -> list[Node]:
    rng = random.Random(seed)
    scenes = _build_forest(rng)
    num_interior = len(scenes)
    terminal_idx = num_interior

    tree: list[Node] = []
    used_pairs: set[tuple[int, int]] = set()

    for i in range(num_interior):
        c1 = rng.randint(0, terminal_idx)
        c2 = rng.randint(0, terminal_idx)
        while (i, c1) == (i, c2) or (c1, c2) in used_pairs:
            c2 = rng.randint(0, terminal_idx)
        used_pairs.add((c1, c2))

        prompt = rng.choice([
            f"{scenes[i]} Press 1 to go forward. Press 2 to go back.",
            f"{scenes[i]} Press 1 to investigate. Press 2 to retreat.",
            f"{scenes[i]} Press 1 to follow the sound. Press 2 to hold position.",
        ])
        tree.append(ChoiceNode(text=prompt, choices={"1": c1, "2": c2}))

    tree.append(TerminalNode(text="A voice on the other end reads four digits. Listen carefully."))
    return tree


def handle(ctx: RoguelikeContext, code: str, seed: int | None = None, max_depth: int = 20) -> dict[str, list[str] | list[int]]:
    tree = make_tree(seed=seed)
    path: list[str] = []
    nodes_visited: list[int] = []
    idx = 0

    while len(path) < max_depth:
        node = tree[idx]
        nodes_visited.append(idx)
        ctx.speak(node["text"])

        if _is_terminal(node):
            ctx.speak(f"The code is {code}. Hang up and dial it now.")
            return {"path": path, "nodes_visited": nodes_visited}

        choices = cast(ChoiceNode, node)["choices"]
        choice = ctx.read_choice("".join(choices.keys()))
        if choice not in choices:
            continue

        path.append(choice)
        idx = choices[choice]

    ctx.speak(f"The code is {code}. Hang up and dial it now.")
    return {"path": path, "nodes_visited": nodes_visited}
