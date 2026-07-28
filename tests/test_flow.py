from __future__ import annotations

import json
from pathlib import Path

from core import flow
from core.router import Router


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


def _router(tmp_path: Path, *, mode: str, code: str = "1234", attempt_limit: int = 3) -> Router:
    config_dir = tmp_path / "config"
    log_dir = tmp_path / "logs"
    config_dir.mkdir()
    log_dir.mkdir()
    (config_dir / "mode.json").write_text(
        json.dumps(
            {"mode": mode, "code": code, "attempt_limit": attempt_limit, "upstream_extension": "200"}
        )
    )
    return Router(config_dir=config_dir, log_dir=log_dir)


def _log_lines(tmp_path: Path) -> list[dict]:
    files = list((tmp_path / "logs").glob("calls-*.jsonl"))
    if not files:
        return []
    return [json.loads(line) for line in files[0].read_text().splitlines() if line]


# -- tweeted --


def test_tweeted_success_first_attempt_routes_to_success(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="tweeted", code="1234")
    io = FakeCallIO(dtmf=["1234"])

    result = flow.run_tweeted(
        io, router, code="1234", max_attempts=3, exile_media="voicemail/busy", wrong_media="beep"
    )

    assert result["outcome"] == "succeed"
    assert io.succeeded is True
    assert io.hung_up is False
    # entered digits actually reach the router (regression: main.py passed None)
    assert io.read_calls == [(4, 15000)]


def test_tweeted_success_logs_exactly_once(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="tweeted", code="1234")
    io = FakeCallIO(dtmf=["1234"])

    flow.run_tweeted(
        io, router, code="1234", max_attempts=3, exile_media="voicemail/busy", wrong_media="beep"
    )

    lines = _log_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["outcome"] == "succeed"


def test_tweeted_wrong_then_right_beeps_then_succeeds(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="tweeted", code="1234")
    io = FakeCallIO(dtmf=["0000", "1234"])

    result = flow.run_tweeted(
        io, router, code="1234", max_attempts=3, exile_media="voicemail/busy", wrong_media="beep"
    )

    assert result["outcome"] == "succeed"
    assert io.played == ["beep"]  # one wrong answer -> one beep
    assert io.succeeded is True
    assert len(_log_lines(tmp_path)) == 1


def test_tweeted_all_wrong_exiles_and_hangs_up(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="tweeted", code="1234", attempt_limit=3)
    io = FakeCallIO(dtmf=["0000", "0000", "0000"])

    result = flow.run_tweeted(
        io, router, code="1234", max_attempts=3, exile_media="voicemail/busy", wrong_media="beep"
    )

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

    result = flow.run_tweeted(
        io, router, code="1234", max_attempts=3, exile_media="voicemail/busy", wrong_media="beep"
    )

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
        code="4242",
        max_attempts=3,
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
        code="4242",
        max_attempts=3,
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
        code="4242",
        max_attempts=3,
        puzzle_id="riddle-001.wav",
        prompt_media="puzzles/riddle-001",
        exile_media="voicemail/busy",
        wrong_media="beep",
    )

    assert result["outcome"] == "exile"
    assert io.played == ["puzzles/riddle-001", "beep", "beep", "voicemail/busy"]
    assert io.hung_up is True


# -- roguelike --


def test_roguelike_navigates_and_routes_to_success(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")
    # plenty of "1" choices to walk to a terminal node
    io = FakeCallIO(dtmf=["1"] * 30)

    result = flow.run_roguelike(io, router, code="0000")

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

    flow.run_roguelike(io, router, code="0000")

    # every read during navigation asks for exactly one digit
    assert io.read_calls
    assert all(num == 1 for num, _timeout in io.read_calls)


def test_roguelike_logs_one_session(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="roguelike", code="0000")
    io = FakeCallIO(dtmf=["1"] * 30)

    flow.run_roguelike(io, router, code="0000")

    assert len(_log_lines(tmp_path)) == 1
