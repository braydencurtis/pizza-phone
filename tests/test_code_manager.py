from __future__ import annotations

import json
from pathlib import Path

from agi.code_manager import read_code, read_mode, update_code, update_mode


def _write_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mode": "tweeted", "code": "1234", "attempt_limit": 3}))


class TestCodeManager:

    def test_read_code(self, tmp_path: Path) -> None:
        config_path = tmp_path / "mode.json"
        _write_config(config_path)
        assert read_code(config_path) == "1234"

    def test_update_code(self, tmp_path: Path) -> None:
        config_path = tmp_path / "mode.json"
        _write_config(config_path)
        update_code(config_path, "9999")
        assert read_code(config_path) == "9999"

    def test_read_mode(self, tmp_path: Path) -> None:
        config_path = tmp_path / "mode.json"
        _write_config(config_path)
        assert read_mode(config_path) == "tweeted"

    def test_update_mode(self, tmp_path: Path) -> None:
        config_path = tmp_path / "mode.json"
        _write_config(config_path)
        update_mode(config_path, "puzzle")
        assert read_mode(config_path) == "puzzle"
