from __future__ import annotations

from agi.mode_roguelike import handle, make_tree


class MockContext:
    def __init__(self, choices: list[str]) -> None:
        self.choices = choices
        self.idx = 0
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def read_choice(self, keys: str) -> str:
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


def test_handle_collects_path_to_terminal() -> None:
    ctx = MockContext(choices=["1", "1", "1"])
    path = handle(ctx, "1234")
    assert path == ["1", "1", "1"]


def test_handle_terminates_at_terminal_node() -> None:
    ctx = MockContext(choices=["1", "1", "1"])
    handle(ctx, "1234")
    assert any("hang up" in s.lower() for s in ctx.spoken)


def test_handle_invalid_choice_repeats_node() -> None:
    ctx = MockContext(choices=["9", "1", "1", "1"])
    path = handle(ctx, "1234")
    assert path == ["1", "1", "1"]
    assert len(ctx.spoken) > 3


def test_handle_asks_for_valid_keys() -> None:
    ctx = MockContext(choices=["1", "1", "1"])
    handle(ctx, "1234")
    assert len(ctx.spoken) >= 4


def test_path_deterministic_for_same_choices() -> None:
    choices = ["1", "1", "1"]
    path_a = handle(MockContext(choices=choices), "1234")
    path_b = handle(MockContext(choices=choices), "5678")
    assert path_a == path_b


def test_alternate_path_via_vent() -> None:
    ctx = MockContext(choices=["2", "1", "1"])
    path = handle(ctx, "1234")
    assert path == ["2", "1", "1"]
    assert len(ctx.spoken) == 5
