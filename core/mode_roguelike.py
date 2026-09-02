"""The Roguelike Phone-Tree: an infinite DTMF maze with no lives and no limit.

The tree is regenerated per Call Session, so node indices mean nothing outside
one call. The walk ends one of two ways: the caller reaches the leaf and has the
Code read to them, or they go quiet and the call is over — silence is the caller
having gone, the same rule the other two Modes hold (see ``core.flow``, #53).
"""

from __future__ import annotations

import random
from typing import Protocol, TypedDict, TypeGuard, cast

from core.observer import NULL_OBSERVER, CallObserver
from core.types import WalkOutcome


class RoguelikeContext(Protocol):
    def speak(self, text: str) -> None: ...
    def read_choice(self, keys: str) -> str: ...


class ChoiceNode(TypedDict):
    text: str
    choices: dict[str, int]


class TerminalNode(TypedDict):
    text: str


Node = ChoiceNode | TerminalNode


class Walk(TypedDict):
    """What one caller did in the maze.

    ``outcome`` is how the walk ended: ``succeed`` if the Code was read out
    (at the leaf, or at the depth bound), ``hangup`` if the caller went silent.
    ``path`` is the keys they pressed and ``nodes_visited`` the rooms they stood
    in — rooms, not turns round the loop, so a caller who fumbles a key is not
    reported as having paced the corridor twice.
    """

    outcome: WalkOutcome
    path: list[str]
    nodes_visited: list[int]


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


def handle(
    ctx: RoguelikeContext,
    code: str,
    seed: int | None = None,
    max_depth: int = 20,
    observer: CallObserver = NULL_OBSERVER,
) -> Walk:
    """Walk the caller through the maze until they reach the leaf or leave."""
    tree = make_tree(seed=seed)
    path: list[str] = []
    nodes_visited: list[int] = []
    idx = 0
    # Whether this turn is an arrival or a replay of the room the caller is
    # already standing in. A refused key replays; only arrivals join the walk.
    # The alternative — appending where `idx` moves — would list a room the
    # depth bound stops the caller from ever reaching.
    arrived = True

    while len(path) < max_depth:
        node = tree[idx]
        terminal = _is_terminal(node)
        # Depth is the moves *made* — `len(path)`, not the times round this
        # loop. An unrecognised key replays the node without advancing, and
        # counting that would show the Operator a caller descending steadily
        # through a maze they are in fact stuck in. The index is carried too,
        # but means nothing outside this call: the tree is regenerated per Call
        # Session. The replay is still reported, because the caller really is
        # hearing the room again — it is the walk, below, that must not grow.
        observer.node_entered(idx, len(path), terminal)
        if arrived:
            nodes_visited.append(idx)
        ctx.speak(node["text"])

        if terminal:
            return _deliver(ctx, code, path, nodes_visited)

        choices = cast(ChoiceNode, node)["choices"]
        choice = ctx.read_choice("".join(choices.keys()))
        if not choice:
            # Silence: the read timed out having heard nothing, which every
            # Mode reads as the caller having gone. Asking again would be
            # asking an empty booth, and this maze has no depth bound to stop
            # it — no move is made, so `max_depth` never moves either. A
            # handset left off the hook would hold the one call slot the engine
            # has and every caller behind it would be hung up on (#53).
            return Walk(outcome="hangup", path=path, nodes_visited=nodes_visited)
        if choice not in choices:
            # A key that *was* pressed: somebody is there, they just missed. Ask
            # again, from the same room, and do not count it against them —
            # the maze has no attempt limit.
            arrived = False
            continue

        arrived = True
        path.append(choice)
        idx = choices[choice]

    return _deliver(ctx, code, path, nodes_visited)


def _deliver(
    ctx: RoguelikeContext, code: str, path: list[str], nodes_visited: list[int]
) -> Walk:
    """Read the Code out and end the walk — the one way to win the maze."""
    ctx.speak(f"The code is {code}. Hang up and dial it now.")
    return Walk(outcome="succeed", path=path, nodes_visited=nodes_visited)
