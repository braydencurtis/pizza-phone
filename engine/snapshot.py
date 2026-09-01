"""The telemetry snapshot the Operator Console is rendered from (ticket #35).

The console is fed *whole-state* snapshots, never deltas: the Global Config the
booth is set to, plus the live Call Session if one is in progress — or an idle
marker when none is. One builder turns the two in-memory objects into a
JSON-ready dict, so the WebSocket server (and any future consumer) has exactly
one place that knows the wire shape. The ``schema`` number lets a future
console tell an old shape from a new one without guessing.
"""

from __future__ import annotations

from typing import Any

from core.config import ConfigSnapshot
from core.types import Mode
from engine.call_session import CallSession

SNAPSHOT_SCHEMA_VERSION = 1


def build_snapshot(config: ConfigSnapshot, session: CallSession | None) -> dict[str, Any]:
    """The full console state: Global Config plus the live call, or idle."""
    return {
        "schema": SNAPSHOT_SCHEMA_VERSION,
        "config": _config_view(config),
        "call": _call_view(session),
    }


def _config_view(config: ConfigSnapshot) -> dict[str, Any]:
    return {
        "mode": config.mode,
        "code": config.code,
        "attempt_limit": config.attempt_limit,
        "upstream_extension": config.upstream_extension,
    }


def _call_view(session: CallSession | None) -> dict[str, Any] | None:
    if session is None:
        return None
    mode: Mode | None = session.mode
    if mode is None and session.config is not None:
        # The mode is stamped at pickup; before that the Config Snapshot taken
        # at that moment is the only record of the game this call was given.
        mode = session.config.mode
    return {
        "session_id": session.session_id,
        "mode": mode,
        "caller_id": session.caller_id,
        "started_at": session.started_at.isoformat(),
        "attempts": session.attempts,
        "outcome": session.outcome,
    }
