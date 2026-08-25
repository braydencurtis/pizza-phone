"""Global Config: the Config Snapshot a call is judged against, and atomic writes."""

from __future__ import annotations

import dataclasses
import json
import os
import stat
import threading
from pathlib import Path
from typing import Any

import pytest

from core.config import ConfigSnapshot, read_raw, take_snapshot, write_config


def _write(path: Path, **overrides: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "mode": "tweeted",
        "code": "1234",
        "attempt_limit": 3,
        "upstream_extension": "200",
        "tts_backend": None,
    }
    config.update(overrides)
    path.write_text(json.dumps(config, indent=2))
    return path


class TestTakeSnapshot:

    def test_captures_mode_code_and_attempt_limit(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mode.json", mode="puzzle", code="4417", attempt_limit=5)

        snapshot = take_snapshot(path)

        assert snapshot.mode == "puzzle"
        assert snapshot.code == "4417"
        assert snapshot.attempt_limit == 5

    def test_captures_the_upstream_extension(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mode.json", upstream_extension="6001")

        assert take_snapshot(path).upstream_extension == "6001"

    def test_missing_keys_fall_back_to_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "mode.json"
        path.write_text(json.dumps({"code": "9999"}))

        snapshot = take_snapshot(path)

        assert snapshot.mode == "tweeted"
        assert snapshot.code == "9999"
        assert snapshot.attempt_limit == 3
        assert snapshot.upstream_extension == "200"

    def test_unknown_mode_is_rejected(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mode.json", mode="unknown_mode")

        with pytest.raises(ValueError, match="unknown_mode"):
            take_snapshot(path)

    def test_a_snapshot_cannot_be_built_on_an_unknown_mode(self) -> None:
        """The invariant is the type's, so nothing downstream re-checks it."""
        with pytest.raises(ValueError, match="unknown_mode"):
            ConfigSnapshot(mode="unknown_mode", code="1234")  # type: ignore[arg-type]

    def test_snapshot_is_frozen(self, tmp_path: Path) -> None:
        snapshot = take_snapshot(_write(tmp_path / "mode.json"))

        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.code = "0000"  # type: ignore[misc]

    def test_a_later_write_does_not_change_a_taken_snapshot(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mode.json", code="4417")
        snapshot = take_snapshot(path)

        write_config(path, {**read_raw(path), "code": "0000", "mode": "roguelike"})

        assert snapshot.code == "4417"
        assert snapshot.mode == "tweeted"

    def test_read_raw_preserves_unknown_keys(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mode.json", operator_note="do not lose me")

        assert read_raw(path)["operator_note"] == "do not lose me"


class TestWriteConfig:

    def test_round_trips_through_a_snapshot(self, tmp_path: Path) -> None:
        path = tmp_path / "mode.json"

        write_config(path, {"mode": "puzzle", "code": "4417", "attempt_limit": 2})

        snapshot = take_snapshot(path)
        assert (snapshot.mode, snapshot.code, snapshot.attempt_limit) == ("puzzle", "4417", 2)

    def test_preserves_keys_the_snapshot_does_not_carry(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mode.json", tts_backend="say")

        write_config(path, {**read_raw(path), "code": "4417"})

        assert read_raw(path)["tts_backend"] == "say"

    def test_leaves_no_temp_files_behind(self, tmp_path: Path) -> None:
        path = tmp_path / "mode.json"

        write_config(path, {"mode": "tweeted", "code": "1234"})

        assert [p.name for p in tmp_path.iterdir()] == ["mode.json"]

    def test_keeps_the_permissions_the_config_file_already_had(self, tmp_path: Path) -> None:
        """An atomic replace must not lock the booth's config down to 0600."""
        path = _write(tmp_path / "mode.json")
        path.chmod(0o644)

        write_config(path, {"mode": "tweeted", "code": "4417"})

        assert stat.S_IMODE(path.stat().st_mode) == 0o644

    def test_a_new_config_file_is_world_readable(self, tmp_path: Path) -> None:
        path = tmp_path / "mode.json"

        write_config(path, {"mode": "tweeted", "code": "4417"})

        assert stat.S_IMODE(path.stat().st_mode) & 0o044

    def test_a_failed_write_leaves_the_previous_config_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _write(tmp_path / "mode.json", code="4417")

        def boom(src: Any, dst: Any) -> None:
            raise OSError("no space left on device")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            write_config(path, {"mode": "tweeted", "code": "0000"})

        assert take_snapshot(path).code == "4417"
        assert [p.name for p in tmp_path.iterdir()] == ["mode.json"]

    def test_a_concurrent_reader_never_sees_a_partial_file(self, tmp_path: Path) -> None:
        """The point of the atomic write: rotating mid-call can't crash a live read."""
        path = _write(tmp_path / "mode.json", code="0000")
        # Big enough that a truncate-and-write would span several page writes.
        padding = "x" * 200_000
        stop = threading.Event()
        errors: list[Exception] = []

        def writer() -> None:
            try:
                for i in range(200):
                    code = f"{i % 10000:04d}"
                    write_config(path, {"mode": "tweeted", "code": code, "pad": padding})
            except OSError as exc:  # pragma: no cover - surfaced via errors
                errors.append(exc)
            finally:
                stop.set()

        def reader() -> None:
            try:
                while not stop.is_set():
                    json.loads(path.read_text())["code"]
            # A truncated file is what this test exists to rule out: partial
            # JSON raises ValueError, a written-but-empty object KeyError.
            except (ValueError, KeyError, OSError) as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
