"""Tests for the headless maze simulator (``core.headless``).

It had none, which is part of how it came to report every simulated walk as a
win: nothing asserted otherwise, and its one caller — ``Router.dispatch``, until
#56 — wanted the lie. Now that no live call reaches it, what it is for is
measuring the maze in bulk, and a measurement that cannot see a loss is no
measurement at all.
"""

from __future__ import annotations

from core import headless, mode_roguelike
from tests.mazes import CYCLING_SEED, SOLVING_SEED

# Both seeds describe a caller who mashes "1", which is what `strategy="first"`
# simulates — so `tests/mazes.py` describes these walks exactly.


def test_a_simulated_walk_that_finds_the_room_is_a_win() -> None:
    walk = headless.run_roguelike("0000", seed=SOLVING_SEED, strategy="first")

    assert walk["outcome"] == "succeed"
    assert any("hang up and dial" in line.lower() for line in walk["spoken"])


def test_a_simulated_walk_that_runs_the_bound_out_is_reported_as_a_loss() -> None:
    """The lie #56 removed: this used to come back ``succeed``."""
    walk = headless.run_roguelike("0000", seed=CYCLING_SEED, strategy="first")

    assert walk["outcome"] == "exile"
    assert not any("0000" in line for line in walk["spoken"])


def test_a_lost_walk_is_bounded_by_the_walk_bound() -> None:
    walk = headless.run_roguelike("0000", seed=CYCLING_SEED, strategy="first")

    assert len(walk["path"]) == 20
    assert walk["attempts"] == len(walk["path"])


def test_a_seeded_simulation_repeats_itself() -> None:
    """Bulk measurement needs a run somebody else can reproduce."""
    first = headless.run_roguelike("0000", seed=7)
    again = headless.run_roguelike("0000", seed=7)

    assert first == again


def test_mashing_one_key_and_pressing_at_random_are_different_walks() -> None:
    masher = headless.run_roguelike("0000", seed=SOLVING_SEED, strategy="first")
    random_walker = headless.run_roguelike("0000", seed=SOLVING_SEED)

    assert masher["path"] == ["1", "1"]
    assert set(random_walker["path"]) <= {"1", "2"}


def test_the_rooms_reported_are_the_rooms_stood_in() -> None:
    """One more room than moves: the mouth of the maze is not walked into."""
    walk = headless.run_roguelike("0000", seed=SOLVING_SEED, strategy="first")

    assert walk["nodes_visited"] == [0, 1, 5]
    assert len(walk["nodes_visited"]) == len(walk["path"]) + 1


def test_the_simulator_is_the_real_walker_on_the_real_generator() -> None:
    """It measures the maze callers get, or it measures nothing."""
    simulated = headless.run_roguelike("0000", seed=SOLVING_SEED, strategy="first")

    ctx = headless.HeadlessRoguelikeContext(strategy="first")
    direct = mode_roguelike.handle(ctx, "0000", seed=SOLVING_SEED)

    assert simulated["path"] == direct["path"]
    assert simulated["nodes_visited"] == direct["nodes_visited"]
    assert simulated["outcome"] == direct["outcome"]
