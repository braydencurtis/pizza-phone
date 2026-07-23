from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class CallSessionLogger:

    log_file_name: str

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        today = datetime.now().__class__.now()
        self.log_file_name = f"calls-{today.strftime('%Y-%m-%d')}.jsonl"

    def log(self, session: dict[str, Any]) -> None:
        serialized = self._serialize(session)
        log_path = self.log_dir / self.log_file_name
        with open(log_path, "a") as f:
            f.write(serialized + "\n")

    def _serialize(self, session: dict[str, Any]) -> str:
        data = dict(session)
        if isinstance(data.get("timestamp"), datetime):
            data["timestamp"] = data["timestamp"].isoformat()
        return json.dumps(data, separators=(",", ":"))
