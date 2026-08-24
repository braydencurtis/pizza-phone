"""Global Config: what the booth is set to, and the snapshot a call is judged against.

Global Config — the active Mode, the current Code, the Attempt Limit — lives in
one JSON file (``config/mode.json``) that the Operator writes at any time. A
Call Session does **not** read that file as it goes. It takes a
:class:`ConfigSnapshot` at pickup and is judged against that snapshot for its
whole duration, so an Operator rotating the Code mid-call lands on the *next*
caller and never on the one already listening. Without it a caller can be told
they are wrong for correctly answering the riddle they were played.

Writes go through :func:`write_config`, which replaces the file atomically, so a
call taking its snapshot at the same moment can never read a truncated file.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

from core.types import Mode

CONFIG_FILENAME = "mode.json"

VALID_MODES: tuple[Mode, ...] = get_args(Mode)

DEFAULT_MODE: Mode = "tweeted"
DEFAULT_CODE = "0000"
DEFAULT_ATTEMPT_LIMIT = 3
DEFAULT_UPSTREAM_EXTENSION = "200"


@dataclass(frozen=True)
class ConfigSnapshot:
    """The copy of Global Config one Call Session is judged against.

    Frozen on purpose: a snapshot handed to a live call is the game that caller
    was given, and nothing downstream may edit it. Take a new one for the next
    call rather than mutating this one.
    """

    mode: Mode
    code: str
    attempt_limit: int = DEFAULT_ATTEMPT_LIMIT
    upstream_extension: str = DEFAULT_UPSTREAM_EXTENSION

    def __post_init__(self) -> None:
        # No snapshot with a mode nothing can play may exist, so nothing
        # downstream has to re-check: a bad Mode fails at pickup, on the one
        # call that read it, rather than deep in a handler.
        if self.mode not in VALID_MODES:
            raise ValueError(f"Unknown mode: {self.mode!r}")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ConfigSnapshot:
        """Build a snapshot from raw config, filling in defaults for absent keys."""
        return cls(
            mode=data.get("mode", DEFAULT_MODE),
            code=str(data.get("code", DEFAULT_CODE)),
            attempt_limit=int(data.get("attempt_limit", DEFAULT_ATTEMPT_LIMIT)),
            upstream_extension=str(data.get("upstream_extension", DEFAULT_UPSTREAM_EXTENSION)),
        )


def take_snapshot(path: Path) -> ConfigSnapshot:
    """Read Global Config as the snapshot a Call Session runs against."""
    return ConfigSnapshot.from_mapping(read_raw(path))


def read_raw(path: Path) -> dict[str, Any]:
    """Read Global Config as a plain dict, unknown keys and all.

    For read-modify-write of the file itself (code rotation, mode switching);
    a Call Session wants :func:`take_snapshot` instead.
    """
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a config object")
    return data


def write_config(path: Path, config: Mapping[str, Any]) -> None:
    """Write Global Config so a concurrent reader never sees a partial file.

    The Operator can rotate the Code while a call is live and that call may take
    its snapshot mid-write; a plain truncate-and-write would occasionally hand
    it an empty or half-written file and crash it. So the new config is written
    to a temp file in the same directory and moved into place with
    ``os.replace`` — a reader sees either the whole old file or the whole new
    one, never a state in between. A failed write leaves the previous config
    untouched. (The directory entry itself is not fsynced: this guards concurrent
    readers, not a machine losing power mid-rotation.)

    The replaced file keeps the permissions the old one had, so rotating the
    Code can't quietly lock the booth's own config out from under whichever
    account the engine runs as (``mkstemp`` alone would leave it 0600).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(config), indent=2) + "\n"

    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.chmod(tmp_name, _permissions_for(path))
        os.replace(tmp_name, path)
    except BaseException:
        # The move never happened, so the old config is still in place; drop
        # the temp file rather than leaving litter next to it.
        try:
            os.unlink(tmp_name)
        except OSError:  # pragma: no cover - already gone
            pass
        raise


def _permissions_for(path: Path) -> int:
    """The permissions a rewritten config should end up with.

    The existing file's, if there is one; otherwise what a plain ``open(...,
    "w")`` would have created under the current umask.
    """
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        return 0o666 & ~umask
