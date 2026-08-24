"""Field-level edits to Global Config: rotate the Code, switch the Mode.

Thin helpers over :mod:`core.config` for the Operator's write path (today
``tools/rotate.py``, in Phase 2 the Console). Every write goes through
``core.config.write_config``, so it replaces the file atomically and a call
taking its Config Snapshot at that moment cannot read a half-written file.
Unknown keys in the file are preserved, and reads here are deliberately
unvalidated: the booth's config is hand-edited, and rotating the Code has to
work even when some other key is wrong. Validation belongs at pickup, where a
Call Session takes its Config Snapshot.
"""

from __future__ import annotations

from pathlib import Path

from core.config import DEFAULT_CODE, DEFAULT_MODE, VALID_MODES, read_raw, write_config


def read_code(config_path: Path) -> str:
    """The Code the next Call Session will be judged against."""
    return str(read_raw(config_path).get("code", DEFAULT_CODE))


def update_code(config_path: Path, new_code: str) -> None:
    """Rotate the Code. Takes effect on the next call, not any live one."""
    _update(config_path, code=new_code)


def read_mode(config_path: Path) -> str:
    """The Mode the next Call Session will run."""
    return str(read_raw(config_path).get("mode", DEFAULT_MODE))


def update_mode(config_path: Path, new_mode: str) -> None:
    """Switch Mode. Takes effect on the next call, not any live one."""
    _update(config_path, mode=_valid_mode(new_mode))


def update_mode_and_code(config_path: Path, new_mode: str, new_code: str) -> None:
    """Switch Mode and rotate the Code in one write, so no call sees a mix."""
    _update(config_path, mode=_valid_mode(new_mode), code=new_code)


def _valid_mode(mode: str) -> str:
    """Reject an unplayable Mode here rather than at some later pickup.

    A typo written to Global Config would otherwise take the booth down one
    call later — every Call Session raises when it takes its snapshot — with
    nothing at the point of the mistake to say so.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown mode: {mode!r}")
    return mode


def _update(config_path: Path, **fields: str) -> None:
    """Read-modify-write Global Config, keeping every other key intact."""
    write_config(config_path, {**read_raw(config_path), **fields})
