from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import flow, mode_roguelike
from core.config import take_snapshot, write_config
from core.router import Router
from tests.mazes import CYCLING_SEED, SOLVING_SEED, WINNING_KEYS_ON_CYCLING_TREE


class FakeCallIO:
    """In-memory CallIO for driving the flow in tests.

    ``dtmf`` is a queue of digit strings returned by successive read_dtmf
    calls; an exhausted queue returns "" (the caller-hung-up signal).
    """

    def __init__(self, dtmf: list[str] | None = None) -> None:
        self._dtmf = list(dtmf or [])
        self.played: list[str] = []
        self.spoken: list[str] = []
        self.read_calls: list[tuple[int, int]] = []
        self.hung_up = False
        self.succeeded = False

    def play(self, media: str) -> None:
        self.played.append(media)

    def read_dtmf(self, num_digits: int, timeout_ms: int) -> str:
        self.read_calls.append((num_digits, timeout_ms))
        return self._dtmf.pop(0) if self._dtmf else ""

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def hangup(self) -> None:
        self.hung_up = True

    def to_success(self) -> None:
        self.succeeded = True


def _config_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "mode.json"


def _write_config(tmp_path: Path, *, mode: str, code: str, attempt_limit: int = 3) -> None:
    write_config(
        _config_path(tmp_path),
        {"mode": mode, "code": code, "attempt_limit": attempt_limit, "upstream_extension": "200"},
    )


def _router(tmp_path: Path, *, mode: str, code: str = "1234", attempt_limit: int = 3) -> Router:
    """A router carrying the Config Snapshot a Call Session picked up with."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    _write_config(tmp_path, mode=mode, code=code, attempt_limit=attempt_limit)
    return Router(take_snapshot(_config_path(tmp_path)), log_dir=log_dir)


def _log_lines(tmp_path: Path) -> list[dict]:
    files = list((tmp_path / "logs").glob("calls-*.jsonl"))
    if not files:
        return []
    return [json.loads(line) for line in files[0].read_text().splitlines() if line]


# -- tweeted --


def test_tweeted_success_first_attempt_routes_to_success(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="tweeted", code="1234")
    io = FakeCallIO(dtmf=["1234"])

    result = flow.run_tweeted(io, router, exile_media="voicemail/busy", wrong_media="beep")

    assert result["outcome"] == "succeed"
    assert io.succeeded is True
    assert io.hung_up is False
    # entered digits actually reach the router (regression: main.py passed None)
    assert io.read_calls == [(4, 15000)]


def test_tweeted_success_logs_exactly_once(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="tweeted", code="1234")
    io = FakeCallIO(dtmf=["1234"])

    flow.run_tweeted(io, router, exile_media="voicemail/busy", wrong_media="beep")

    lines = _log_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["outcome"] == "succeed"


def test_tweeted_wrong_then_right_beeps_then_succeeds(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="tweeted", code="1234")
    io = FakeCallIO(dtmf=["0000", "1234"])

    result = flow.run_tweeted(io, router, exile_media="voicemail/busy", wrong_media="beep")

    assert result["outcome"] == "succeed"
    assert io.played == ["beep"]  # one wrong answer -> one beep
    assert io.succeeded is True
    assert len(_log_lines(tmp_path)) == 1


def test_tweeted_all_wrong_exiles_and_hangs_up(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="tweeted", code="1234", attempt_limit=3)
    io = FakeCallIO(dtmf=["0000", "0000", "0000"])

    result = flow.run_tweeted(io, router, exile_media="voicemail/busy", wrong_media="beep")

    assert result["outcome"] == "exile"
    assert result["attempts"] == 3
    assert io.played == ["beep", "beep", "voicemail/busy"]
    assert io.hung_up is True
    assert io.succeeded is False
    lines = _log_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["outcome"] == "exile"


def test_tweeted_no_input_hangs_up_without_logging(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="tweeted", code="1234")
    io = FakeCallIO(dtmf=[])  # caller enters nothing

    result = flow.run_tweeted(io, router, exile_media="voicemail/busy", wrong_media="beep")

    assert result["outcome"] == "hangup"
    assert io.hung_up is True
    assert io.succeeded is False
    assert _log_lines(tmp_path) == []


# -- puzzle --


def test_puzzle_plays_prompt_then_accepts_answer(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="puzzle", code="4242")
    io = FakeCallIO(dtmf=["4242"])

    result = flow.run_puzzle(
        io,
        router,
        puzzle_id="riddle-001.wav",
        prompt_media="puzzles/riddle-001",
        exile_media="voicemail/busy",
        wrong_media="beep",
    )

    assert result["outcome"] == "succeed"
    assert io.played[0] == "puzzles/riddle-001"  # prompt played before answer collection
    assert io.succeeded is True
    assert io.read_calls == [(4, 30000)]


def test_puzzle_logs_puzzle_id(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="puzzle", code="4242")
    io = FakeCallIO(dtmf=["4242"])

    flow.run_puzzle(
        io,
        router,
        puzzle_id="riddle-007.wav",
        prompt_media="puzzles/riddle-007",
        exile_media="voicemail/busy",
        wrong_media="beep",
    )

    lines = _log_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["puzzle_id"] == "riddle-007.wav"


def test_puzzle_exile_after_max_attempts(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="puzzle", code="4242", attempt_limit=3)
    io = FakeCallIO(dtmf=["0000", "1111", "2222"])

    result = flow.run_puzzle(
        io,
        router,
        puzzle_id="riddle-001.wav",
        prompt_media="puzzles/riddle-001",
        exile_media="voicemail/busy",
        wrong_media="beep",
    )

    assert result["outcome"] == "exile"
    assert io.played == ["puzzles/riddle-001", "beep", "beep", "voicemail/busy"]
    assert io.hung_up is True


# -- roguelike --
#
# The tree is regenerated per Call Session, so a walk on an unseeded tree can
# end either way — and since #59 the two endings differ. Every test that cares
# which one it got seeds the tree; `tests/mazes.py` says which tree is which.


def test_roguelike_navigates_and_routes_to_success(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")
    # plenty of "1" choices to walk to a terminal node
    io = FakeCallIO(dtmf=["1"] * 30)

    result = flow.run_roguelike(io, router, seed=SOLVING_SEED)

    assert result["mode"] == "roguelike"
    # the tree speaks prompts to the caller
    assert len(io.spoken) >= 2
    # the spoken code delivery happened
    assert any("hang up and dial" in s.lower() for s in io.spoken)
    if result["outcome"] == "succeed":
        assert io.succeeded is True


def test_roguelike_reads_single_digit_choices(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = FakeCallIO(dtmf=["1"] * 30)

    flow.run_roguelike(io, router)

    # every read during navigation asks for exactly one digit
    assert io.read_calls
    assert all(num == 1 for num, _timeout in io.read_calls)


def test_roguelike_logs_one_session(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = FakeCallIO(dtmf=["1"] * 30)

    flow.run_roguelike(io, router)

    assert len(_log_lines(tmp_path)) == 1


# -- Config Snapshot isolation (#34) --


def test_a_code_rotated_mid_call_does_not_change_what_the_caller_is_judged_against(
    tmp_path: Path,
) -> None:
    """The bug from the booth: right answer, rotated code, told they are wrong."""
    router = _router(tmp_path, mode="puzzle", code="4417")

    class RotatingIO(FakeCallIO):
        """Rotates the Code the way the Operator would, between two attempts."""

        def read_dtmf(self, num_digits: int, timeout_ms: int) -> str:
            if self.read_calls:  # after the caller's first wrong answer
                _write_config(tmp_path, mode="roguelike", code="8080")
            return super().read_dtmf(num_digits, timeout_ms)

    io = RotatingIO(dtmf=["0000", "4417"])
    result = flow.run_puzzle(
        io,
        router,
        puzzle_id="riddle-001.wav",
        prompt_media="puzzles/riddle-001",
        exile_media="voicemail/busy",
        wrong_media="beep",
    )

    # Judged against the riddle they were played, not the freshly rotated code.
    assert result["outcome"] == "succeed"
    assert result["mode"] == "puzzle"
    assert io.succeeded is True


def test_the_next_call_picks_up_the_rotated_code(tmp_path: Path) -> None:
    live = _router(tmp_path, mode="tweeted", code="1234")
    _write_config(tmp_path, mode="tweeted", code="8080")
    next_call = Router(take_snapshot(_config_path(tmp_path)), log_dir=tmp_path / "logs")

    live_result = flow.run_tweeted(
        FakeCallIO(dtmf=["1234"]), live, exile_media="voicemail/busy", wrong_media="beep"
    )
    next_result = flow.run_tweeted(
        FakeCallIO(dtmf=["8080"]), next_call, exile_media="voicemail/busy", wrong_media="beep"
    )

    assert live_result["outcome"] == "succeed"
    assert next_result["outcome"] == "succeed"


# -- the CallObserver seam (#37) --
#
# Attempt number, roguelike node and the selected puzzle live inside these flow
# functions, and the flow returns exactly once — at the terminal outcome — so
# nothing about them escaped mid-call. `CallObserver` is the way out. It is
# deliberately *not* `CallIO`: those five methods are all "talk to the caller",
# and this is "talk to the Operator" (ADR-0003).


class RecordingObserver:
    """A CallObserver that just remembers what it was told, in order."""

    def __init__(self) -> None:
        self.attempts: list[tuple[int, int]] = []
        self.nodes: list[tuple[int, int, bool]] = []
        self.puzzles: list[str] = []

    def attempt_started(self, attempt: int, limit: int) -> None:
        self.attempts.append((attempt, limit))

    def node_entered(self, node: int, depth: int, terminal: bool) -> None:
        self.nodes.append((node, depth, terminal))

    def puzzle_selected(self, puzzle_id: str) -> None:
        self.puzzles.append(puzzle_id)


def test_tweeted_reports_each_attempt_as_it_starts(tmp_path: Path) -> None:
    """The count the Operator watches climb toward Exile."""
    observer = RecordingObserver()
    io = FakeCallIO(dtmf=["1111", "2222", "1234"])
    flow.run_tweeted(
        io,
        _router(tmp_path, mode="tweeted", code="1234"),
        exile_media="exile",
        wrong_media="wrong",
        observer=observer,
    )
    # Announced before the read, not after the verdict: the Operator should see
    # "attempt 3 of 3" while the caller is dialling it, not once it is over.
    assert observer.attempts == [(1, 3), (2, 3), (3, 3)]


def test_the_reported_limit_is_the_one_this_call_is_judged_against(tmp_path: Path) -> None:
    """The call's frozen Config Snapshot, not whatever Global Config says now."""
    observer = RecordingObserver()
    io = FakeCallIO(dtmf=["9999"])
    flow.run_tweeted(
        io,
        _router(tmp_path, mode="tweeted", code="1234", attempt_limit=1),
        exile_media="exile",
        wrong_media="wrong",
        observer=observer,
    )
    assert observer.attempts == [(1, 1)]


def test_an_attempt_the_caller_never_makes_is_not_reported(tmp_path: Path) -> None:
    """Silence is a hangup, and a hangup is not a burned attempt."""
    observer = RecordingObserver()
    io = FakeCallIO(dtmf=["1111"])
    flow.run_tweeted(
        io,
        _router(tmp_path, mode="tweeted", code="1234"),
        exile_media="exile",
        wrong_media="wrong",
        observer=observer,
    )
    # Attempt 2 is announced — the caller was asked — but there is no third:
    # the read came back empty and the flow hung up.
    assert observer.attempts == [(1, 3), (2, 3)]


def test_puzzle_reports_which_riddle_the_caller_got(tmp_path: Path) -> None:
    observer = RecordingObserver()
    io = FakeCallIO(dtmf=["1234"])
    flow.run_puzzle(
        io,
        _router(tmp_path, mode="puzzle", code="1234"),
        puzzle_id="riddle-07.wav",
        prompt_media="prompt",
        exile_media="exile",
        wrong_media="wrong",
        observer=observer,
    )
    assert observer.puzzles == ["riddle-07.wav"]
    assert observer.attempts == [(1, 3)]


def test_roguelike_reports_each_node_as_the_caller_reaches_it(tmp_path: Path) -> None:
    """The maze has no map, so the Console needs the walk narrated."""
    observer = RecordingObserver()
    io = FakeCallIO(dtmf=["1"] * 20)
    flow.run_roguelike(io, _router(tmp_path, mode="roguelike"), observer=observer)

    assert observer.nodes, "the walk should have been reported"
    first_node, first_depth, _ = observer.nodes[0]
    assert (first_node, first_depth) == (0, 0), "a walk starts at the root"
    # Depth counts the rooms walked through, so it climbs by one per step.
    assert [depth for _, depth, _ in observer.nodes] == list(range(len(observer.nodes)))
    # Reaching the leaf is the one position that matters most — it is where the
    # Code is read out — so it is called out rather than left to be inferred.
    assert [terminal for _, _, terminal in observer.nodes[:-1]] == [False] * (
        len(observer.nodes) - 1
    )


def test_a_wrong_key_does_not_sink_the_caller_deeper_into_the_maze(tmp_path: Path) -> None:
    """Depth is rooms walked, not times round the loop.

    An unrecognised key leaves the caller exactly where they were — the node is
    replayed and they are asked again. Counting that as a step would show the
    Operator a caller descending steadily through a maze they are in fact stuck
    in, which is worse than showing nothing.
    """
    observer = RecordingObserver()
    # "9" is not a choice on any node, so the walk should not move for it. The
    # "1"s after it are what lets the call end: an unrecognised key replays the
    # node forever (see the note in the roguelike walker), so a queue that runs
    # dry here would never return.
    io = FakeCallIO(dtmf=["9", "9", "9", *["1"] * 40])
    flow.run_roguelike(io, _router(tmp_path, mode="roguelike"), observer=observer)

    depths = [depth for _, depth, _ in observer.nodes]
    assert depths[:4] == [0, 0, 0, 0], f"three refused keys should not advance: {depths}"
    # And the node itself does not move either.
    assert len({node for node, _, _ in observer.nodes[:4]}) == 1


def test_every_flow_still_runs_with_no_observer_at_all(tmp_path: Path) -> None:
    """`core/` stays usable, and testable, without one — the seam is optional."""
    tweeted = FakeCallIO(dtmf=["1234"])
    flow.run_tweeted(
        tweeted, _router(tmp_path, mode="tweeted"), exile_media="e", wrong_media="w"
    )
    assert tweeted.succeeded

    puzzle = FakeCallIO(dtmf=["1234"])
    flow.run_puzzle(
        puzzle,
        _router(tmp_path, mode="puzzle"),
        puzzle_id="p.wav",
        prompt_media="prompt",
        exile_media="e",
        wrong_media="w",
    )
    assert puzzle.succeeded

    roguelike = FakeCallIO(dtmf=["1"] * 20)
    flow.run_roguelike(roguelike, _router(tmp_path, mode="roguelike"))
    assert roguelike.spoken


# -- silence ends a call, in every Mode (#53) --------------------------------
#
# The Booth Phone holds one call at a time: a session that never ends does not
# just waste a call, it makes the engine hang up on everybody behind it until
# the handset is physically replaced. Tweeted and Audio Puzzle already read an
# empty ``read_dtmf`` as the caller having gone; the maze read it as "not a key
# I recognise" and asked the same room again every 15 seconds, forever. These
# tests pin the rule down as one rule, held by all three Modes.


class SilentCallIO(FakeCallIO):
    """A caller who picks up and never presses anything.

    ``FakeCallIO`` already returns ``""`` from an exhausted queue, which is the
    signal itself. The bound is so the bug this guards fails the suite instead
    of hanging it: before the fix the maze asks this caller forever.
    """

    PATIENCE = 20

    def __init__(self) -> None:
        super().__init__(dtmf=[])

    def read_dtmf(self, num_digits: int, timeout_ms: int) -> str:
        if len(self.read_calls) >= self.PATIENCE:
            raise AssertionError(
                f"a silent caller was asked {self.PATIENCE} times — the flow is looping"
            )
        return super().read_dtmf(num_digits, timeout_ms)


def test_a_silent_caller_in_the_maze_hangs_up_instead_of_looping(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = SilentCallIO()

    result = flow.run_roguelike(io, router)

    assert result["outcome"] == "hangup"
    assert io.hung_up is True
    assert io.succeeded is False


def test_a_silent_caller_in_the_maze_is_never_read_the_code(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = SilentCallIO()

    flow.run_roguelike(io, router)

    assert not any("0000" in line for line in io.spoken)


def test_a_silent_maze_caller_is_not_logged_as_a_played_session(tmp_path: Path) -> None:
    """Same as the other Modes: a caller who walked away has nothing to log."""
    router = _router(tmp_path, mode="roguelike", code="0000")

    flow.run_roguelike(SilentCallIO(), router)

    assert _log_lines(tmp_path) == []


def test_silence_ends_a_call_the_same_way_in_all_three_modes(tmp_path: Path) -> None:
    """The invariant the maze broke: one rule for silence, held everywhere."""
    tweeted_io = SilentCallIO()
    tweeted = flow.run_tweeted(
        tweeted_io, _router(tmp_path, mode="tweeted"), exile_media="e", wrong_media="w"
    )
    puzzle_io = SilentCallIO()
    puzzle = flow.run_puzzle(
        puzzle_io,
        _router(tmp_path, mode="puzzle"),
        puzzle_id="p.wav",
        prompt_media="prompt",
        exile_media="e",
        wrong_media="w",
    )
    roguelike_io = SilentCallIO()
    roguelike = flow.run_roguelike(roguelike_io, _router(tmp_path, mode="roguelike"))

    assert [tweeted["outcome"], puzzle["outcome"], roguelike["outcome"]] == ["hangup"] * 3
    assert [tweeted_io.hung_up, puzzle_io.hung_up, roguelike_io.hung_up] == [True] * 3
    assert [tweeted_io.succeeded, puzzle_io.succeeded, roguelike_io.succeeded] == [False] * 3


def test_a_fat_finger_in_the_maze_is_still_forgiven(tmp_path: Path) -> None:
    """A key that *is* pressed is not silence — the room is simply asked again."""
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = FakeCallIO(dtmf=["9", "0", "9", *["1"] * 40])

    result = flow.run_roguelike(io, router, seed=SOLVING_SEED)

    assert result["outcome"] != "hangup"
    assert any("hang up and dial" in line.lower() for line in io.spoken)


def test_a_silent_maze_caller_reports_the_walk_they_actually_made(tmp_path: Path) -> None:
    """No pacing: a caller who never moved does not report rooms they never left."""
    observer = RecordingObserver()
    io = SilentCallIO()

    flow.run_roguelike(io, _router(tmp_path, mode="roguelike"), observer=observer)

    assert [depth for _, depth, _ in observer.nodes] == [0]


# -- a wedged key ends a call too (#55) --------------------------------------
#
# The other half of the door #53 closed. Silence is the caller gone; a key that
# *is* pressed but is not one the room offers is a caller fumbling, and stays
# forgiven. What is not forgiven forever is the same refused key coming back
# turn after turn in one room — a wedged handset key, or anything on the line
# decoding as a digit — which replayed that room every 15 seconds for as long as
# the booth stayed off the hook, holding its one call slot against everybody
# behind it. The bound is liveness, not lives: it notices nobody is choosing.


class WedgedKeyCallIO(FakeCallIO):
    """A caller whose handset hands back the same refused digit forever.

    Not silence — every read returns a real key — so nothing #53 built catches
    this one. The patience bound is what makes the bug fail the suite instead of
    hanging it.
    """

    PATIENCE = 40

    def __init__(self, digit: str = "9") -> None:
        super().__init__()
        self._digit = digit

    def read_dtmf(self, num_digits: int, timeout_ms: int) -> str:
        if len(self.read_calls) >= self.PATIENCE:
            raise AssertionError(
                f"a wedged key was read {self.PATIENCE} times — the flow is looping"
            )
        # The read itself is real — the parent records it — and what comes back
        # is the wedged key rather than the empty queue's silence.
        super().read_dtmf(num_digits, timeout_ms)
        return self._digit


def test_a_wedged_key_in_the_maze_hangs_up_instead_of_looping(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = WedgedKeyCallIO()

    result = flow.run_roguelike(io, router)

    assert result["outcome"] == "hangup"
    assert io.hung_up is True
    assert io.succeeded is False


def test_a_wedged_key_caller_is_never_read_the_code(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = WedgedKeyCallIO()

    flow.run_roguelike(io, router)

    assert not any("0000" in line for line in io.spoken)


def test_a_wedged_key_caller_is_not_logged_as_a_played_session(tmp_path: Path) -> None:
    """Nobody chose anything, so there is no session to judge — as with silence."""
    router = _router(tmp_path, mode="roguelike", code="0000")

    flow.run_roguelike(WedgedKeyCallIO(), router)

    assert _log_lines(tmp_path) == []


def test_a_wedged_key_caller_reports_only_the_room_they_stood_in(tmp_path: Path) -> None:
    observer = RecordingObserver()

    flow.run_roguelike(
        WedgedKeyCallIO(), _router(tmp_path, mode="roguelike"), observer=observer
    )

    # The room is replayed — the caller really is hearing it again — but they
    # never left it, so every report is the same room at the same depth.
    assert {(node, depth) for node, depth, _ in observer.nodes} == {(0, 0)}


def test_the_booth_takes_the_next_caller_after_a_wedged_key(tmp_path: Path) -> None:
    """The point of the bound: the slot is free again without a restart."""
    wedged = WedgedKeyCallIO()
    flow.run_roguelike(wedged, _router(tmp_path, mode="roguelike", code="0000"))

    after = FakeCallIO(dtmf=["1"] * 40)
    result = flow.run_roguelike(
        after, _router(tmp_path, mode="roguelike", code="0000"), seed=SOLVING_SEED
    )

    assert result["outcome"] == "succeed"
    assert after.succeeded is True


def test_a_fat_finger_in_every_room_is_still_forgiven(tmp_path: Path) -> None:
    """Fumbling once per room is not a wedged key, however many rooms there are."""
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = FakeCallIO(dtmf=["9", "1", "0", "1", "9", "1", "#", "1", *["1"] * 40])

    result = flow.run_roguelike(io, router, seed=SOLVING_SEED)

    assert result["outcome"] != "hangup"
    assert any("hang up and dial" in line.lower() for line in io.spoken)


# -- the maze can beat you (#59) ---------------------------------------------
#
# The walk is bounded, and running it out used to deliver the Code anyway — so
# the maze paid out however the walk ended and could not beat anybody who kept
# pressing keys it offered. It is Exile now: a flavoured disconnect, no Code,
# and a *logged* session, because unlike a hangup this caller played and lost.
#
# The tree is random per call, so these seed it — see `tests/mazes.py`.

def test_running_the_maze_out_exiles_the_caller(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = FakeCallIO(dtmf=["1"] * 40)

    result = flow.run_roguelike(io, router, seed=CYCLING_SEED)

    assert result["outcome"] == "exile"
    assert io.hung_up is True
    assert io.succeeded is False


def test_an_exiled_maze_caller_is_never_read_the_code(tmp_path: Path) -> None:
    """The point of the change: the maze stops giving the Code away."""
    io = FakeCallIO(dtmf=["1"] * 40)

    flow.run_roguelike(io, _router(tmp_path, mode="roguelike", code="0000"), seed=CYCLING_SEED)

    assert not any("0000" in line for line in io.spoken)
    assert not any("hang up and dial" in line.lower() for line in io.spoken)


def test_an_exiled_maze_caller_is_logged_because_they_played(tmp_path: Path) -> None:
    """The line between Exile and a hangup: one of them played and lost."""
    router = _router(tmp_path, mode="roguelike", code="0000")

    flow.run_roguelike(FakeCallIO(dtmf=["1"] * 40), router, seed=CYCLING_SEED)

    lines = _log_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["outcome"] == "exile"
    assert lines[0]["mode"] == "roguelike"


def test_an_exiled_maze_record_holds_the_walk_the_caller_made(tmp_path: Path) -> None:
    """Their rooms, not a simulation's — the half of #56 this ending needs."""
    router = _router(tmp_path, mode="roguelike", code="0000")

    result = flow.run_roguelike(FakeCallIO(dtmf=["1"] * 40), router, seed=CYCLING_SEED)

    assert result["path"] == ["1"] * 20  # every key they pressed, to the bound
    assert result["attempts"] == 20
    assert _log_lines(tmp_path)[0]["path"] == ["1"] * 20


def test_a_caller_who_finds_the_room_still_wins(tmp_path: Path) -> None:
    """Nothing changes for a caller who solves it."""
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = FakeCallIO(dtmf=["1"] * 40)

    result = flow.run_roguelike(io, router, seed=SOLVING_SEED)

    assert result["outcome"] == "succeed"
    assert io.succeeded is True
    assert io.hung_up is False
    assert any("hang up and dial" in line.lower() for line in io.spoken)


def test_a_caller_who_leaves_mid_maze_is_gone_not_exiled(tmp_path: Path) -> None:
    """Silence is still somebody walking away, and still not logged."""
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = FakeCallIO(dtmf=["1"])  # one room in, then the handset goes quiet

    result = flow.run_roguelike(io, router, seed=CYCLING_SEED)

    assert result["outcome"] == "hangup"
    assert _log_lines(tmp_path) == []


# -- a won walk is the caller's own (#56) ------------------------------------
#
# `Router.dispatch` used to reach `core.headless` for roguelike and simulate a
# whole fresh walk on a whole fresh tree, picking each key at random, and the
# record it built from that simulation was what got logged and persisted. So a
# won maze call carried a path nobody walked, and — because the simulated
# walker always reached a leaf — an outcome that was `succeed` whatever the
# caller had done. `CYCLING_SEED` is the sharpest tree to show this on: mashing
# "1" loops there forever, so the keys below are the only reason this walk wins.

WINNING_KEYS = WINNING_KEYS_ON_CYCLING_TREE


def test_a_won_walk_records_the_keys_the_caller_pressed(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")

    result = flow.run_roguelike(
        FakeCallIO(dtmf=WINNING_KEYS), router, seed=CYCLING_SEED
    )

    assert result["outcome"] == "succeed"
    assert result["path"] == WINNING_KEYS
    assert _log_lines(tmp_path)[0]["path"] == WINNING_KEYS


def test_a_won_walk_records_the_rooms_the_caller_stood_in(tmp_path: Path) -> None:
    """The rooms go to the history, as they already did for an Exiled walk."""
    router = _router(tmp_path, mode="roguelike", code="0000")

    flow.run_roguelike(FakeCallIO(dtmf=WINNING_KEYS), router, seed=CYCLING_SEED)

    assert _log_lines(tmp_path)[0]["nodes_visited"] == [0, 3, 1, 4, 5]


def test_attempts_on_a_won_walk_is_the_moves_the_caller_made(tmp_path: Path) -> None:
    """`attempts` is the maze's move count — what the Walk Bound counts."""
    router = _router(tmp_path, mode="roguelike", code="0000")

    result = flow.run_roguelike(
        FakeCallIO(dtmf=WINNING_KEYS), router, seed=CYCLING_SEED
    )

    assert result["attempts"] == len(WINNING_KEYS)
    assert _log_lines(tmp_path)[0]["attempts"] == len(WINNING_KEYS)


def test_the_same_tree_wins_or_exiles_on_the_caller_s_keys_alone(tmp_path: Path) -> None:
    """The inversion #56 fixes, in one test: one tree, two callers, two endings.

    Under the simulation both of these were `succeed`, both carried a random
    walker's path, and the history could not tell them apart.
    """
    won = flow.run_roguelike(
        FakeCallIO(dtmf=WINNING_KEYS),
        _router(tmp_path, mode="roguelike", code="0000"),
        seed=CYCLING_SEED,
    )
    masher = flow.run_roguelike(
        FakeCallIO(dtmf=["1"] * 40),
        _router(tmp_path, mode="roguelike", code="0000"),
        seed=CYCLING_SEED,
    )

    assert [won["outcome"], masher["outcome"]] == ["succeed", "exile"]
    assert won["path"] != masher["path"]


def test_no_walk_is_simulated_during_a_live_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One tree per Call Session — the caller's. The second one was the bug.

    Counting `make_tree` is the cheapest way to say "nothing walked a maze
    behind the caller's back": the simulation could not run without building
    itself a tree first.
    """
    built: list[int | None] = []
    real = mode_roguelike.make_tree

    def counting_make_tree(seed: int | None = None) -> list[mode_roguelike.Node]:
        built.append(seed)
        return real(seed=seed)

    monkeypatch.setattr(mode_roguelike, "make_tree", counting_make_tree)

    flow.run_roguelike(
        FakeCallIO(dtmf=WINNING_KEYS),
        _router(tmp_path, mode="roguelike", code="0000"),
        seed=CYCLING_SEED,
    )

    assert built == [CYCLING_SEED]
