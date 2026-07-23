from __future__ import annotations

import json
from pathlib import Path

from agi.router import Router


def _write_config(config_dir: Path, mode: str = "tweeted", code: str = "1234") -> None:
    config_dir.mkdir(exist_ok=True)
    (config_dir / "mode.json").write_text(
        json.dumps({"mode": mode, "code": code, "attempt_limit": 3, "upstream_extension": "200"})
    )


def _make_router(config_dir: Path, log_dir: Path) -> Router:
    return Router(config_dir=config_dir, log_dir=log_dir)


class TestRouter:

    def test_reads_config(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_config(config_dir, mode="puzzle", code="9999")

        router = _make_router(config_dir, log_dir)
        config = router.load_config()
        assert config["mode"] == "puzzle"
        assert config["code"] == "9999"

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
        result = router.dispatch(answer="7890")
        assert result["outcome"] in ("succeed", "fail")
        assert result["mode"] == "puzzle"

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

    def test_unknown_mode_raises_value_error(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        log_dir = tmp_path / "logs"
        config_dir.mkdir()
        log_dir.mkdir()
        (config_dir / "mode.json").write_text(
            json.dumps({"mode": "unknown_mode", "code": "1234", "attempt_limit": 3})
        )

        router = _make_router(config_dir, log_dir)
        try:
            router.dispatch()
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "unknown_mode" in str(e)
