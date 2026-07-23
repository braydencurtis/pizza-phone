from __future__ import annotations

import json
from pathlib import Path

from agi.mode_puzzle import PuzzleSelector, handle


def _make_pool(tmp_path: Path, names: list[str]) -> Path:
    pool = tmp_path / "puzzles"
    pool.mkdir()
    for name in names:
        (pool / name).write_text("fake wav")
    return pool


# -- PuzzleSelector tests --


class TestPuzzleSelector:

    def test_picks_random_wav(self, tmp_path: Path) -> None:
        pool = _make_pool(tmp_path, ["riddle-001.wav", "riddle-002.wav", "riddle-003.wav"])
        selector = PuzzleSelector(pool, seed=42)
        picked = selector.pick()
        assert picked.suffix == ".wav"
        assert picked.name in ("riddle-001.wav", "riddle-002.wav", "riddle-003.wav")

    def test_deterministic_with_seed(self, tmp_path: Path) -> None:
        pool = _make_pool(tmp_path, ["riddle-001.wav", "riddle-002.wav"])
        s1 = PuzzleSelector(pool, seed=10)
        s2 = PuzzleSelector(pool, seed=10)
        assert s1.pick() == s2.pick()

    def test_ignores_non_wav_files(self, tmp_path: Path) -> None:
        pool = _make_pool(tmp_path, ["riddle-001.wav"])
        (pool / "notes.txt").write_text("ignore me")
        (pool / "riddle-002.mp3").write_text("ignore me")
        selector = PuzzleSelector(pool)
        picked = selector.pick()
        assert picked.name == "riddle-001.wav"

    def test_raises_on_empty_pool(self, tmp_path: Path) -> None:
        pool = tmp_path / "puzzles"
        pool.mkdir()
        selector = PuzzleSelector(pool)
        try:
            selector.pick()
            assert False, "Expected FileNotFoundError"
        except FileNotFoundError:
            pass


# -- handle() tests --


class TestHandle:

    def test_correct_answer_succeeds(self) -> None:
        result = handle(answer="4242", expected_code="4242", attempt=1, max_attempts=3, puzzle_id="riddle-001.wav")
        assert result["outcome"] == "succeed"
        assert result["attempts"] == 1
        assert result["puzzle_id"] == "riddle-001.wav"

    def test_wrong_answer_first_attempt_returns_fail(self) -> None:
        result = handle(answer="0000", expected_code="4242", attempt=1, max_attempts=3, puzzle_id="riddle-001.wav")
        assert result["outcome"] == "fail"
        assert result["attempts"] == 1

    def test_wrong_answer_at_max_attempts_returns_exile(self) -> None:
        result = handle(answer="0000", expected_code="4242", attempt=3, max_attempts=3, puzzle_id="riddle-001.wav")
        assert result["outcome"] == "exile"
        assert result["attempts"] == 3

    def test_correct_answer_on_last_attempt_still_succeeds(self) -> None:
        result = handle(answer="4242", expected_code="4242", attempt=3, max_attempts=3, puzzle_id="riddle-002.wav")
        assert result["outcome"] == "succeed"
        assert result["attempts"] == 3

    def test_puzzle_id_propagates_through_outcomes(self) -> None:
        for outcome_want in ("succeed", "fail", "exile"):
            if outcome_want == "succeed":
                r = handle(answer="1234", expected_code="1234", attempt=1, max_attempts=3, puzzle_id="riddle-x.wav")
            elif outcome_want == "fail":
                r = handle(answer="0000", expected_code="1234", attempt=1, max_attempts=3, puzzle_id="riddle-x.wav")
            else:
                r = handle(answer="0000", expected_code="1234", attempt=3, max_attempts=3, puzzle_id="riddle-x.wav")
            assert r["puzzle_id"] == "riddle-x.wav", f"Expected puzzle_id for {outcome_want}"
            assert r["outcome"] == outcome_want
