from __future__ import annotations

import json
from pathlib import Path

from core.config import take_snapshot, write_config
from core.router import Router


def _write_config(config_dir: Path, mode: str = "tweeted", code: str = "1234") -> None:
    write_config(
        config_dir / "mode.json",
        {"mode": mode, "code": code, "attempt_limit": 3, "upstream_extension": "200"},
    )


def _make_router(config_dir: Path, log_dir: Path) -> Router:
    """A router judging against the Config Snapshot taken from the config file."""
    return Router(take_snapshot(config_dir / "mode.json"), log_dir=log_dir)


class TestRouter:

    def test_judges_against_its_snapshot(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="puzzle", code="9999")

        router = _make_router(config_dir, log_dir)
        assert router.config.mode == "puzzle"
        assert router.config.code == "9999"

    def test_dispatches_to_tweeted(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="tweeted", code="1234")

        router = _make_router(config_dir, log_dir)
        result = router.dispatch(code_attempt="1234")
        assert result["outcome"] == "succeed"
        assert result["mode"] == "tweeted"

    def test_dispatches_to_puzzle(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="puzzle", code="7890")

        router = _make_router(config_dir, log_dir)
        result = router.dispatch(answer="7890", puzzle_id="riddle-001.wav")
        assert result["outcome"] in ("succeed", "fail")
        assert result["mode"] == "puzzle"

    def test_puzzle_requires_puzzle_id(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="puzzle", code="7890")

        router = _make_router(config_dir, log_dir)
        try:
            router.dispatch(answer="7890")
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "puzzle_id" in str(e)

    def test_dispatches_to_roguelike(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="roguelike", code="0000")

        router = _make_router(config_dir, log_dir)
        result = router.dispatch(path=[])
        assert result["mode"] == "roguelike"

    def test_logs_session_after_dispatch(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="tweeted", code="1234")

        router = _make_router(config_dir, log_dir)
        router.dispatch(code_attempt="1234")

        log_files = list(log_dir.glob("calls-*.jsonl"))
        assert len(log_files) == 1
        entry = json.loads(log_files[0].read_text().strip())
        assert entry["mode"] == "tweeted"
        assert entry["outcome"] == "succeed"

    def test_wrong_code_results_in_fail(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="tweeted", code="1234")

        router = _make_router(config_dir, log_dir)
        result = router.dispatch(code_attempt="0000")
        assert result["outcome"] == "fail"
        assert result["mode"] == "tweeted"

    def test_puzzle_succeed_returns_puzzle_id(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="puzzle", code="4242")

        router = _make_router(config_dir, log_dir)
        result = router.dispatch(answer="4242", puzzle_id="riddle-001.wav")
        assert result["outcome"] == "succeed"
        assert result["puzzle_id"] == "riddle-001.wav"

    def test_puzzle_exile_on_max_attempts(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="puzzle", code="4242")

        router = _make_router(config_dir, log_dir)
        result = router.dispatch(answer="0000", attempt=3, puzzle_id="riddle-002.wav")
        assert result["outcome"] == "exile"
        assert result["attempts"] == 3

    def test_puzzle_fail_returns_fail_on_attempt_below_max(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="puzzle", code="4242")

        router = _make_router(config_dir, log_dir)
        result = router.dispatch(answer="0000", attempt=2, puzzle_id="riddle-003.wav")
        assert result["outcome"] == "fail"
        assert result["attempts"] == 2

    def test_puzzle_log_contains_puzzle_id(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="puzzle", code="7890")

        router = _make_router(config_dir, log_dir)
        router.dispatch(answer="7890", puzzle_id="riddle-001.wav")

        log_files = list(log_dir.glob("calls-*.jsonl"))
        assert len(log_files) == 1
        entry = json.loads(log_files[0].read_text().strip())
        assert entry["puzzle_id"] == "riddle-001.wav"

    def test_log_false_skips_logging(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="tweeted", code="1234")

        router = _make_router(config_dir, log_dir)
        router.dispatch(code_attempt="1234", log=False)

        log_files = list(log_dir.glob("calls-*.jsonl"))
        assert len(log_files) == 0


class TestConfigSnapshotIsolation:
    """A live Call Session is judged against the game it was given (#34)."""

    def test_rotating_the_code_mid_session_does_not_change_judging(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="puzzle", code="4417")
        router = _make_router(config_dir, log_dir)

        # The Operator rotates the Code while the caller is still on the line.
        _write_config(config_dir, mode="puzzle", code="8080")

        # The caller dials the answer to the riddle they were actually played.
        result = router.dispatch(answer="4417", attempt=1, puzzle_id="riddle-001.wav")
        assert result["outcome"] == "succeed"
        # ...and the freshly rotated Code is not what they are scored against.
        rotated = router.dispatch(answer="8080", attempt=2, puzzle_id="riddle-001.wav")
        assert rotated["outcome"] == "fail"

    def test_switching_mode_mid_session_does_not_change_the_callers_mode(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="tweeted", code="1234")
        router = _make_router(config_dir, log_dir)

        _write_config(config_dir, mode="roguelike", code="1234")

        result = router.dispatch(code_attempt="1234")
        assert result["mode"] == "tweeted"

    def test_a_mid_call_change_applies_to_the_next_session(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="tweeted", code="1234")
        live_router = _make_router(config_dir, log_dir)

        _write_config(config_dir, mode="tweeted", code="8080")
        next_router = _make_router(config_dir, log_dir)

        assert live_router.dispatch(code_attempt="1234")["outcome"] == "succeed"
        assert next_router.dispatch(code_attempt="8080")["outcome"] == "succeed"
        assert next_router.dispatch(code_attempt="1234")["outcome"] == "fail"
