from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agi.logger import CallSessionLogger
from agi.types import Mode, Outcome

# Use plain str values — the types are documented in types.py


def _session(
    mode: str = "tweeted",
    outcome: str = "succeed",
    duration: int = 30,
    attempts: int = 1,
    path: list[str] | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp or datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "mode": mode,
        "outcome": outcome,
        "duration": duration,
        "attempts": attempts,
        "path": path or [],
    }


class TestCallSessionLogger:

    def test_logs_single_session_to_json_lines(
        self, tmp_path: Path
    ) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        logger = CallSessionLogger(log_dir)

        logger.log(_session())

        log_file = log_dir / logger.log_file_name
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["mode"] == "tweeted"
        assert entry["outcome"] == "succeed"
        assert entry["duration"] == 30
        assert entry["attempts"] == 1
        assert entry["path"] == []
        assert "timestamp" in entry

    def test_appends_multiple_sessions(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        logger = CallSessionLogger(log_dir)

        logger.log(_session(mode="tweeted", duration=30))
        logger.log(_session(mode="puzzle", outcome="fail", duration=60, attempts=3))

        log_file = log_dir / logger.log_file_name
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["mode"] == "tweeted"
        assert json.loads(lines[1])["mode"] == "puzzle"

    def test_uses_date_based_log_file_name(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        logger = CallSessionLogger(log_dir)

        # Default log file name uses today's date
        expected = f"calls-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        assert logger.log_file_name == expected

    def test_logs_path_for_roguelike_mode(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        logger = CallSessionLogger(log_dir)

        logger.log(_session(mode="roguelike", duration=300, attempts=0, path=["1", "3", "2"]))

        log_file = log_dir / logger.log_file_name
        entry = json.loads(log_file.read_text().strip())
        assert entry["path"] == ["1", "3", "2"]

    def test_serializes_timestamp_as_iso_string(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        logger = CallSessionLogger(log_dir)

        ts = datetime(2025, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
        logger.log(_session(timestamp=ts))

        log_file = log_dir / logger.log_file_name
        entry = json.loads(log_file.read_text().strip())
        assert entry["timestamp"] == "2025-06-15T08:30:00+00:00"
