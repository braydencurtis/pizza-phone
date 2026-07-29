"""Dev/office run entry point for the Call Engine: ``python -m engine``.

Wires the engine from the environment and the repo layout, then runs it until
interrupted (Ctrl-C / SIGTERM). ARI connection details come from the
environment so no secret lands in the repo; the rest of the runtime config
(mode, code, attempt limit) lives in ``config/mode.json`` and is reloaded per
call.

Environment:

- ``ARI_BASE_URL``   Asterisk ARI base URL (default ``http://localhost:8088``)
- ``ARI_USERNAME``   ARI user (default ``pizza``)
- ``ARI_PASSWORD``   ARI password (default ``pizza``)
- ``ARI_APP``        Stasis application name (default ``pizza-phone``)
- ``PIZZA_DB_PATH``  SQLite call-history file (default ``<repo>/data/calls.db``)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from engine.ari_client import ARIClient
from engine.call_engine import CallEngine
from engine.call_store import CallStore

logger = logging.getLogger(__name__)


def _build_engine() -> tuple[CallEngine, ARIClient]:
    base = Path(__file__).resolve().parent.parent
    ari = ARIClient(
        base_url=os.environ.get("ARI_BASE_URL", "http://localhost:8088"),
        username=os.environ.get("ARI_USERNAME", "pizza"),
        password=os.environ.get("ARI_PASSWORD", "pizza"),
        app=os.environ.get("ARI_APP", "pizza-phone"),
    )
    db_path = Path(os.environ.get("PIZZA_DB_PATH", str(base / "data" / "calls.db")))
    store = CallStore(db_path)
    engine = CallEngine(
        ari,
        store,
        config_dir=base / "config",
        log_dir=base / "logs",
        audio_dir=base / "audio",
    )
    return engine, ari


async def _run() -> None:
    engine, _ = _build_engine()
    await engine.start()
    logger.info("Call engine running — Ctrl-C to stop")
    try:
        # Idle here while the engine services calls on its own tasks; nothing
        # else drives the loop until a signal cancels this wait.
        await asyncio.Event().wait()
    finally:
        await engine.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Interrupted; shutting down")


if __name__ == "__main__":
    main()
