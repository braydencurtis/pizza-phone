from __future__ import annotations

from core.mode_roguelike import EXILE_TEXT, REFUSED_KEYS_BEFORE_GONE, handle, make_tree


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


# -- walking the maze out is a loss (#59) ------------------------------------
#
# The walk is bounded at `max_depth` moves. That bound used to read the Code out
# too, so the maze paid out either way and could not beat anybody who kept
# pressing keys it offered — and on the real generator, 22.7% of walks reached
# the Code that way rather than by finding the room. The caller could not tell
# the two apart: same voice, same four digits. Now running the walk out is
# Exile, and the leaf is the only way to win.


def test_running_the_walk_out_is_an_exile_not_a_win() -> None:
    ctx = MockContext(choices=["1"] * 30)
    result = handle(ctx, "1234", seed=42, max_depth=1)
    assert result["outcome"] == "exile"


def test_a_caller_who_runs_the_walk_out_is_never_read_the_code() -> None:
    """The whole point: the maze stops giving the Code away."""
    ctx = MockContext(choices=["1"] * 30)
    handle(ctx, "1234", seed=42, max_depth=1)
    assert not any("1234" in line for line in ctx.spoken)


def test_the_maze_says_something_before_it_lets_go() -> None:
    """Exile is a flavoured disconnect, not a dead line (CONTEXT.md)."""
    ctx = MockContext(choices=["1"] * 30)
    handle(ctx, "1234", seed=42, max_depth=1)
    assert ctx.spoken[-1] == EXILE_TEXT


def test_an_exiled_walk_still_reports_where_the_caller_went() -> None:
    """They played, and how far they got is the record's whole interest."""
    # Seed 0 is a tree where pressing "1" closes into a loop of two rooms, so
    # this caller walks the bound out without ever nearing the room.
    ctx = MockContext(choices=["1"] * 30)
    result = handle(ctx, "1234", seed=0, max_depth=3)
    assert result["outcome"] == "exile"
    assert result["path"] == ["1", "1", "1"]
    assert result["nodes_visited"][0] == 0


def test_finding_the_room_is_still_the_win_it_was() -> None:
    """Nothing changes for a caller who solves it."""
    ctx = MockContext(choices=["1"] * 5)
    result = handle(ctx, "1234", seed=42)
    assert result["outcome"] == "succeed"
    assert any("1234" in line for line in ctx.spoken)


def test_a_last_move_into_the_room_is_a_win_not_an_exile() -> None:
    """The bound is checked after the room is looked at, not before it.

    Seed 3 puts the room holding the Code exactly two moves from the door. A
    caller who spends their last legal move walking into it has found it — and
    testing the bound first would Exile them while they stood there, without
    the room ever being spoken, so they would not even hear what they reached.
    """
    ctx = MockContext(choices=["1", "1"])
    result = handle(ctx, "1234", seed=3, max_depth=2)
    assert result["outcome"] == "succeed"
    assert any("1234" in line for line in ctx.spoken)


def test_an_exiled_caller_hears_the_room_they_ended_in() -> None:
    """They stop somewhere, and where they stopped is worth narrating."""
    ctx = MockContext(choices=["1"] * 30)
    result = handle(ctx, "1234", seed=0, max_depth=3)
    # Four rooms stood in for three moves made: the door, and one per move.
    assert len(result["nodes_visited"]) == 4
    assert len(ctx.spoken) == 5  # four rooms, then the ending
    assert ctx.spoken[-1] == EXILE_TEXT


def test_a_caller_who_leaves_before_the_bound_is_gone_not_exiled() -> None:
    """Silence is still somebody walking away, not the maze beating them."""
    ctx = MockContext(choices=["1"])
    result = handle(ctx, "1234", seed=42, max_depth=10)
    assert result["outcome"] == "hangup"
    assert EXILE_TEXT not in ctx.spoken


# -- a key that never becomes a choice ends the walk too (#55) ---------------
#
# #53 settled silence; this is the other half. A key that *is* pressed but is
# not one the room offers is forgiven — the room is replayed, nothing is counted
# against the caller — and forgiving it without end is what made it a hazard: a
# wedged key on the handset, or anything on the line that keeps decoding as one,
# replays the same room every 15 seconds forever. `max_depth` bounds moves made,
# and a refused key makes no move, so it could no more stop this than it could
# stop the silent caller. The bound below is liveness, not lives: it is not
# counting wrong answers — the maze has none — it is noticing nobody is
# choosing.


class WedgedKeyContext(MockContext):
    """A caller whose handset hands back the same refused digit forever.

    The patience bound is what makes the bug fail the suite rather than hang it:
    unbounded, the walker asks this caller until the heat death of the booth.
    """

    PATIENCE = 40

    def __init__(self, digit: str = "9") -> None:
        super().__init__(choices=[])
        self._digit = digit
        self.reads = 0

    def read_choice(self, keys: str) -> str:
        self.reads += 1
        if self.reads > self.PATIENCE:
            raise AssertionError(
                f"a wedged key was read {self.PATIENCE} times — the walk is looping"
            )
        return self._digit


def test_a_wedged_key_ends_the_walk_instead_of_replaying_forever() -> None:
    ctx = WedgedKeyContext()
    result = handle(ctx, "1234", seed=42)
    assert result["outcome"] == "hangup"


def test_a_wedged_key_gives_up_after_the_bound_not_before() -> None:
    """The forgiveness is real: the room is asked again, several times over."""
    ctx = WedgedKeyContext()
    handle(ctx, "1234", seed=42)
    assert ctx.reads == REFUSED_KEYS_BEFORE_GONE
    # Every read replayed the room the caller never left.
    assert len(ctx.spoken) == REFUSED_KEYS_BEFORE_GONE
    assert len(set(ctx.spoken)) == 1


def test_a_wedged_key_caller_is_not_read_the_code() -> None:
    ctx = WedgedKeyContext()
    handle(ctx, "1234", seed=42)
    assert not any("1234" in line for line in ctx.spoken)


def test_a_wedged_key_reports_the_walk_the_caller_actually_made() -> None:
    """They never left the first room, so that is the whole walk."""
    ctx = WedgedKeyContext()
    result = handle(ctx, "1234", seed=42)
    assert result["path"] == []
    assert result["nodes_visited"] == [0]


def test_a_key_that_wedges_partway_through_ends_the_walk_where_it_wedged() -> None:
    ctx = MockContext(choices=["1", *["9"] * 40])
    result = handle(ctx, "1234", seed=42)
    assert result["outcome"] == "hangup"
    assert result["path"] == ["1"]
    # The room they chose their way into, and the one they wedged in: two.
    assert len(result["nodes_visited"]) == 2
    assert result["nodes_visited"][0] == 0


def test_the_refused_count_resets_when_the_caller_chooses() -> None:
    """Fumbling in every room is not the same as never choosing in one.

    One key short of the bound, then a choice, in every room of the walk. This
    caller never reaches it. Counting refusals across rooms rather than within
    one would end this walk in the second room, and it is a walk.
    """
    one_room = ["9"] * (REFUSED_KEYS_BEFORE_GONE - 1) + ["1"]
    ctx = MockContext(choices=one_room * 5)
    result = handle(ctx, "1234", seed=42)
    assert result["outcome"] == "succeed"


def test_a_caller_who_fumbles_and_then_chooses_is_unaffected() -> None:
    """The walk the fat-finger caller records is the clean caller's walk."""
    clean = handle(MockContext(choices=["1"] * 5), "1234", seed=42)
    fumbled = handle(MockContext(choices=["9", "1", "9", "9", "1", "1", "1", "1"]), "1234", seed=42)

    assert fumbled["outcome"] == clean["outcome"] == "succeed"
    assert fumbled["nodes_visited"] == clean["nodes_visited"]
    assert fumbled["path"] == clean["path"]
