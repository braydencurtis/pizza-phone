from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


class TestRotate:

    def test_rotate_updates_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "mode.json"
        config_path.write_text(json.dumps({"mode": "tweeted", "code": "0000", "attempt_limit": 3}))

        from agi.rotate import main

        with patch("sys.argv", ["rotate.py", "--mode", "puzzle", "--code", "4242", "--config", str(config_path)]):
            main()

        data = json.loads(config_path.read_text())
        assert data["mode"] == "puzzle"
        assert data["code"] == "4242"
        assert data["attempt_limit"] == 3

    def test_rotate_sends_slack_notification(self, tmp_path: Path) -> None:
        config_path = tmp_path / "mode.json"
        config_path.write_text(json.dumps({"mode": "tweeted", "code": "0000"}))

        from agi.rotate import main

        with patch("sys.argv", ["rotate.py", "--mode", "roguelike", "--code", "9999", "--config", str(config_path)]):
            with patch("agi.slack_notifier.urlopen"):
                main()

    def test_rotate_no_webhook(self, tmp_path: Path) -> None:
        config_path = tmp_path / "mode.json"
        config_path.write_text(json.dumps({"mode": "tweeted", "code": "0000"}))

        from agi.rotate import main

        with (
            patch("sys.argv", ["rotate.py", "--mode", "puzzle", "--code", "1234", "--config", str(config_path)]),
            patch.dict("os.environ", {}, clear=True),
        ):
            main()

        data = json.loads(config_path.read_text())
        assert data["mode"] == "puzzle"
        assert data["code"] == "1234"

    def test_rotate_invalid_mode_fails(self, tmp_path: Path) -> None:
        config_path = tmp_path / "mode.json"
        config_path.write_text(json.dumps({"mode": "tweeted", "code": "0000"}))

        from agi.rotate import main

        with (
            patch("sys.argv", ["rotate.py", "--mode", "invalid", "--code", "1234", "--config", str(config_path)]),
            pytest.raises(SystemExit),
        ):
            main()
