from __future__ import annotations

from core.mode_roguelike import handle, make_tree


class MockContext:
    """A scripted caller.

    An exhausted script returns ``""`` — what a real caller who presses nothing
    gets back once the choice timeout expires, and what the walker reads as the
    caller having gone (#53). Scripting silence is running out of keys.
    """

    def __init__(self, choices: list[str]) -> None:
        self.choices = choices
        self.idx = 0
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def read_choice(self, keys: str) -> str:
        if self.idx >= len(self.choices):
            return ""
        choice = self.choices[self.idx]
        self.idx += 1
        return choice


def test_make_tree_returns_nodes() -> None:
    tree = make_tree()
    assert len(tree) >= 5
    for node in tree:
        assert "text" in node


def test_make_tree_last_node_is_terminal() -> None:
    tree = make_tree()
    last = tree[-1]
    assert "choices" not in last


def test_make_tree_deterministic_with_seed() -> None:
    tree_a = make_tree(seed=42)
    tree_b = make_tree(seed=42)
    assert tree_a == tree_b


def test_make_tree_different_without_seed() -> None:
    tree_a = make_tree()
    tree_b = make_tree()
    assert tree_a != tree_b


def test_handle_collects_path_to_terminal() -> None:
    ctx = MockContext(choices=["1", "1", "1", "1", "1"])
    result = handle(ctx, "1234", seed=42)
    assert len(result["path"]) >= 1


def test_handle_terminates_at_terminal_node() -> None:
    ctx = MockContext(choices=["1", "1", "1", "1", "1"])
    handle(ctx, "1234", seed=42)
    assert any("hang up" in s.lower() for s in ctx.spoken)


def test_handle_invalid_choice_repeats_node() -> None:
    ctx = MockContext(choices=["9", "1", "1", "1", "1", "1"])
    result = handle(ctx, "1234", seed=42)
    assert len(result["path"]) >= 1
    assert len(ctx.spoken) > len(result["path"])


def test_handle_asks_for_valid_keys() -> None:
    ctx = MockContext(choices=["1", "1", "1", "1", "1"])
    handle(ctx, "1234", seed=42)
    assert len(ctx.spoken) >= 2


def test_path_deterministic_for_same_seed() -> None:
    choices = ["1", "1", "1", "1", "1"]
    result_a = handle(MockContext(choices=choices), "1234", seed=42)
    result_b = handle(MockContext(choices=choices), "5678", seed=42)
    assert result_a["path"] == result_b["path"]


def test_alternate_path() -> None:
    ctx = MockContext(choices=["2", "1", "1", "1", "1"])
    result = handle(ctx, "1234", seed=42)
    assert len(result["path"]) >= 1
    assert len(ctx.spoken) >= 2


def test_handle_respects_max_depth() -> None:
    ctx = MockContext(choices=["1"] * 30)
    result = handle(ctx, "1234", seed=42, max_depth=5)
    assert result["outcome"] == "succeed"  # not a caller who ran out of script
    assert len(result["path"]) <= 5


def test_handle_tracks_nodes_visited() -> None:
    ctx = MockContext(choices=["1", "1", "1", "1", "1"])
    result = handle(ctx, "1234", seed=42)
    assert result["outcome"] == "succeed"  # not a caller who ran out of script
    assert "nodes_visited" in result
    assert len(result["nodes_visited"]) >= 1
    assert result["nodes_visited"][0] == 0


def test_handle_nodes_visited_includes_terminal() -> None:
    ctx = MockContext(choices=["1", "1", "1", "1", "1"])
    result = handle(ctx, "1234", seed=42)
    tree = make_tree(seed=42)
    terminal_idx = len(tree) - 1
    assert result["nodes_visited"][-1] == terminal_idx


def test_handle_nodes_visited_deterministic_for_same_seed() -> None:
    choices = ["1", "1", "1", "1", "1"]
    result_a = handle(MockContext(choices=choices), "1234", seed=42)
    result_b = handle(MockContext(choices=choices), "1234", seed=42)
    assert result_a["nodes_visited"] == result_b["nodes_visited"]


def test_a_refused_key_does_not_add_a_room_to_the_walk() -> None:
    """The walk is rooms stood in, not turns round the loop (#53).

    A refused key replays the room the caller is already in, so the two callers
    below stood in exactly the same rooms — one of them just fumbled on the way.
    """
    clean = handle(MockContext(choices=["1", "1", "1", "1", "1"]), "1234", seed=42)
    fumbled = handle(MockContext(choices=["9", "1", "9", "9", "1", "1", "1", "1"]), "1234", seed=42)

    assert fumbled["nodes_visited"] == clean["nodes_visited"]
    assert fumbled["path"] == clean["path"]


def test_handle_path_and_nodes_visited_lengths_match_spoken() -> None:
    ctx = MockContext(choices=["1", "1", "1", "1", "1"])
    result = handle(ctx, "1234", seed=42)
    assert len(result["nodes_visited"]) == len(ctx.spoken) - 1


def test_handle_max_depth_includes_nodes_visited() -> None:
    ctx = MockContext(choices=["1"] * 30)
    result = handle(ctx, "1234", seed=42, max_depth=5)
    assert len(result["nodes_visited"]) <= 6


# -- silence ends the walk (#53) ---------------------------------------------
#
# A caller who presses nothing gets "" back from the choice read. Before #53
# that was simply "not a valid key", so the room was replayed and the caller
# asked again — forever, for a handset left off the hook, holding the booth
# against every caller behind it. Silence is now the caller having gone, the
# rule the other two Modes already followed.


def test_a_silent_caller_ends_the_walk() -> None:
    ctx = MockContext(choices=[])
    result = handle(ctx, "1234", seed=42)
    assert result["outcome"] == "hangup"


def test_a_silent_caller_is_asked_once_not_forever() -> None:
    ctx = MockContext(choices=[])
    handle(ctx, "1234", seed=42)
    # One room narrated, one read, and out — not the same room on a loop.
    assert len(ctx.spoken) == 1


def test_a_silent_caller_is_not_read_the_code() -> None:
    ctx = MockContext(choices=[])
    handle(ctx, "1234", seed=42)
    assert not any("1234" in line for line in ctx.spoken)


def test_silence_partway_through_ends_the_walk_where_the_caller_stopped() -> None:
    """One room in, then the handset goes quiet."""
    ctx = MockContext(choices=["1"])
    result = handle(ctx, "1234", seed=42)
    assert result["outcome"] == "hangup"
    assert result["path"] == ["1"]
    assert not any("1234" in line for line in ctx.spoken)


def test_reaching_the_leaf_is_a_success() -> None:
    ctx = MockContext(choices=["1", "1", "1", "1", "1"])
    result = handle(ctx, "1234", seed=42)
    assert result["outcome"] == "succeed"


def test_the_depth_bound_still_delivers_the_code() -> None:
    """A walk that never finds the leaf is not a caller who walked away."""
    ctx = MockContext(choices=["1"] * 30)
    result = handle(ctx, "1234", seed=42, max_depth=1)
    assert result["outcome"] == "succeed"
    assert any("1234" in line for line in ctx.spoken)
