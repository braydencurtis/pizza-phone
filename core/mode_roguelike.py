"""The Roguelike Phone-Tree: an infinite DTMF maze with no lives and no limit.

The tree is regenerated per Call Session, so node indices mean nothing outside
one call. The walk ends one of three ways: the caller reaches the leaf and has
the Code read to them, they walk the bound out without ever finding it and are
Exiled (#59), or the line stops choosing and the call is over. Silence is
the caller having gone, the same rule the other two Modes hold (see
``core.flow``, #53); so is a key that comes back refused turn after turn in one
room, which is a handset lying on a wedged key rather than a caller (#55).
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

    ``outcome`` is how the walk ended: ``succeed`` if the Code was read out,
    which now happens only at the leaf; ``exile`` if the walk ran out its bound
    without finding it; ``hangup`` if the caller stopped choosing — by going
    silent, or by holding down a key the room never offers.
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


# How many keys in a row one room takes that are not among its choices before
# it stops asking — the fifth such key ends the walk rather than earning a sixth
# ask. Liveness, not lives: it is not counting wrong answers, because the maze
# has none to get wrong, it is noticing that nobody is choosing. Five is far
# past a fat finger and far short of a caller's patience, and a caller who
# fumbles, moves on and fumbles again in the next room never approaches it.
REFUSED_KEYS_BEFORE_GONE = 5

# PLACEHOLDER COPY. The Prompt Library is team-authored (CONTEXT.md), and the
# real words are being written against a working booth rather than in front of
# one (#59). This is here so the mechanic can ship; it is not signed-off lore.
EXILE_TEXT = (
    "You have been walking a long time. The corridor folds back on itself, "
    "the lights go out one at a time, and the hum stops. "
    "There is no code here tonight."
)


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
    # Refused keys since the caller last chose something, reset by every choice
    # they make — so this is bounded within one room, never across the walk.
    refused = 0

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
            return _gone(path, nodes_visited)
        if choice not in choices:
            # A key that *was* pressed: somebody is there, they just missed. Ask
            # again, from the same room, and do not count it against them —
            # the maze has no attempt limit.
            refused += 1
            if refused >= REFUSED_KEYS_BEFORE_GONE:
                # Except that the same refused key, over and over in one room,
                # is not a caller missing: it is a handset lying on a wedged
                # key, or anything on the line that keeps decoding as a digit.
                # Nobody is choosing, so we read it the way we read silence —
                # the caller has gone. Left unbounded this replays one room
                # every 15 seconds forever, and `max_depth` cannot stop it,
                # because a refused key makes no move for it to count (#55).
                return _gone(path, nodes_visited)
            arrived = False
            continue

        arrived = True
        refused = 0
        path.append(choice)
        idx = choices[choice]

    return _exiled(ctx, path, nodes_visited)


def _exiled(ctx: RoguelikeContext, path: list[str], nodes_visited: list[int]) -> Walk:
    """The caller walked the bound out without finding the room: Exile (#59).

    This exit used to run ``_deliver`` as well, so the maze paid out however the
    walk ended and could not beat anybody who kept pressing keys it offered.
    Nor was it a rare consolation: pressing one key repeatedly follows a fixed
    chain through the rooms, and that chain usually closes into a loop of two or
    three of them, so roughly two in three callers who mash a single key end
    here rather than at the leaf. They now leave with the ending and no Code.
    """
    ctx.speak(EXILE_TEXT)
    return Walk(outcome="exile", path=path, nodes_visited=nodes_visited)


def _gone(path: list[str], nodes_visited: list[int]) -> Walk:
    """End the walk on the caller having gone — the one way to leave the maze.

    Silence and a wedged key are the same ending reaching us by two routes, and
    ``core.flow`` treats what comes back as one thing, so this is one function
    rather than two returns that happen to agree. The rooms walked before the
    caller stopped go with it: how far they got is the interesting part of a
    walk that was abandoned.
    """
    return Walk(outcome="hangup", path=path, nodes_visited=nodes_visited)


def _deliver(
    ctx: RoguelikeContext, code: str, path: list[str], nodes_visited: list[int]
) -> Walk:
    """Read the Code out and end the walk — the one way to win the maze."""
    ctx.speak(f"The code is {code}. Hang up and dial it now.")
    return Walk(outcome="succeed", path=path, nodes_visited=nodes_visited)
