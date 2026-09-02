"""The telemetry snapshot the Operator Console is rendered from (#35, #36).

The console is fed *whole-state* snapshots, never deltas: the Global Config the
booth is set to, plus the live Call Session if one is in progress — or an idle
marker when none is. One builder turns the two in-memory objects into a
JSON-ready dict, so the WebSocket server (and any future consumer) has exactly
one place that knows the wire shape. The ``schema`` number lets a future
console tell an old shape from a new one without guessing.

The live call carries ``started_at`` and (once over) ``ended_at``, never a
duration: the browser advances the elapsed clock itself, so a call on the line
does not generate a message per second purely to tick a timer (ADR-0003).

Note the two Attempt Limits, which are not the same number. The one under
``config`` is Global Config — what the booth is set to *now*. The one under
``call`` came off this call's frozen Config Snapshot, and is what the caller on
the line is actually being judged against. They differ for the length of any
call in progress when an Operator changes the setting, and the Console must
never show the first in place of the second.
"""

from __future__ import annotations

from typing import Any

from core.config import ConfigSnapshot
from core.types import Mode
from engine.call_session import CallSession

# 2: the live Call Session gained its state vocabulary, the digits the caller is
#    dialling, and the moment a finished call ended (#36).
# 3: …and its live progress — which attempt of how many, which room of the maze,
#    which riddle — off the CallObserver seam (#37).
# 4: a call that ended without the mode handler returning now carries an outcome
#    too — ``hangup`` for a caller who hung up mid-call, and the new ``dropped``
#    for an engine failure (#50). ``call.outcome`` was always null for those
#    before, and ``dropped`` is a value no older console has in its union.
SNAPSHOT_SCHEMA_VERSION = 4


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
        "state": session.state,
        "mode": mode,
        "caller_id": session.caller_id,
        "started_at": session.started_at.isoformat(),
        # None while the call is live. Present so the Console can stop the
        # elapsed clock where the call stopped rather than at "now".
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "digits": "".join(session.digits),
        # Where the caller is *now*: which attempt of how many they are on.
        # ``attempts`` below is the final count, and stays 0 until the handler
        # returns — the Console shows one during the call and the other after.
        "attempt": session.current_attempt,
        "attempt_limit": _attempt_limit(session),
        "node": _node_view(session),
        "puzzle_id": session.puzzle_id,
        "attempts": session.attempts,
        "outcome": session.outcome,
    }


def _attempt_limit(session: CallSession) -> int | None:
    """The Attempt Limit *this call* is judged against.

    Reported by the flow as each attempt starts, so it is the number actually
    used. Before the first attempt there is nothing to report but the Config
    Snapshot, which is the same number the flow will use — an Operator watching
    a call answer should see "of 3" straight away, not a dash that fills in only
    once the caller dials.
    """
    if session.attempt_limit is not None:
        return session.attempt_limit
    return session.config.attempt_limit if session.config is not None else None


def _node_view(session: CallSession) -> dict[str, Any] | None:
    """Where the caller has got to in the Roguelike Phone-Tree, if anywhere.

    The index is carried for completeness, but ``depth`` is the readable part:
    the tree is regenerated per Call Session, so the index is a coordinate on a
    map only this call has. ``terminal`` is the leaf — where the Code is read
    aloud — which is the one position worth spotting from across a room.
    """
    node = session.node
    if node is None:
        return None
    return {"index": node.index, "depth": node.depth, "terminal": node.terminal}
