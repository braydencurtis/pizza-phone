from __future__ import annotations

from pathlib import Path
from typing import Any


def read_code(config_path: Path) -> str:
    import json

    config = json.loads(config_path.read_text())
    return config.get("code", "0000")


def update_code(config_path: Path, new_code: str) -> None:
    import json

    config = json.loads(config_path.read_text())
    config["code"] = new_code
    config_path.write_text(json.dumps(config, indent=2))


def read_mode(config_path: Path) -> str:
    import json

    config = json.loads(config_path.read_text())
    return config.get("mode", "tweeted")


def update_mode(config_path: Path, new_mode: str) -> None:
    import json

    config = json.loads(config_path.read_text())
    config["mode"] = new_mode
    config_path.write_text(json.dumps(config, indent=2))
