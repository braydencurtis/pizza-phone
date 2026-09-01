"""Dev/office run entry point for the Call Engine: ``python -m engine``.

Wires the engine from the environment and the repo layout, then runs it until
interrupted (Ctrl-C / SIGTERM). ARI connection details come from the
environment so no secret lands in the repo; the rest of the runtime config
(mode, code, attempt limit) lives in ``config/mode.json``, which each call
snapshots at pickup — an Operator write lands on the next call, never the live
one.

Environment:

- ``ARI_BASE_URL``   Asterisk ARI base URL (default ``http://localhost:8088``)
- ``ARI_USERNAME``   ARI user (default ``pizza``)
- ``ARI_PASSWORD``   ARI password (default ``pizza``)
- ``ARI_APP``        Stasis application name (default ``pizza-phone``)
- ``PIZZA_DB_PATH``  SQLite call-history file (default ``<repo>/data/calls.db``)
- ``PIZZA_CONSOLE_PASSWORD``  the Console's shared password (see below)
- ``PIZZA_CONSOLE_HOST``  interface the Console listens on (default ``0.0.0.0``)
- ``PIZZA_CONSOLE_PORT``  Console port (default ``8080``)

**The Operator Console.** The same process serves the Console (#35) — one
process, one port — from the committed bundle in ``web/dist``. It is gated by a
single shared password, taken from ``PIZZA_CONSOLE_PASSWORD`` or, failing that,
``config/console.json`` (gitignored). No password means no console: the engine
refuses to start rather than serving the booth's Code to the LAN, so the way to
run without one is to say so with ``--no-console``. Fake mode is the exception
— its whole world is synthetic, so it falls back to a development password.

**Fake mode.** ``--fake-pbx`` runs the engine against the Fake PBX
(``engine/fake_pbx.py``) instead of Asterisk: synthetic Call Sessions on a
timer, real everything below. It exists to build the Operator Console away from
the booth and is development-only, so it is reachable **only** through that flag
— no environment variable and no config key turns it on — and it refuses to
start when a real ARI connection is configured *or* simply reachable, so it
cannot be run on the rig by habit. Its config, logs, puzzle pool
and call history live in a sandbox (``PIZZA_FAKE_PBX_DIR``, default
``<repo>/data/fake-pbx``), never the booth's.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from core.config import CONFIG_FILENAME, read_raw
from engine.ari_client import ARIClient
from engine.call_engine import CallEngine
from engine.call_store import CallStore
from engine.console import ConsoleServer
from engine.fake_audio import WavTee
from engine.fake_pbx import (
    DEFAULT_INTERVAL_S,
    FakePBX,
    FakePBXHarness,
    FakeWorkspace,
    build_fake_harness,
    prepare_workspace,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Setting any of these means someone has pointed this process at a real PBX.
# Fake mode alongside them is a contradiction we refuse rather than guess at.
ARI_ENV_VARS = ("ARI_BASE_URL", "ARI_USERNAME", "ARI_PASSWORD", "ARI_APP")

DEFAULT_ARI_BASE_URL = "http://localhost:8088"

# The Console's own config file, kept out of Global Config: ``mode.json`` is
# rewritten by code rotation and read by every call, and a password has no
# business in a file that churns. Gitignored — the booth's password is not
# repo content.
CONSOLE_CONFIG_FILENAME = "console.json"

# The LAN, not localhost: the Operator watches from a laptop in the room.
DEFAULT_CONSOLE_HOST = "0.0.0.0"
DEFAULT_CONSOLE_PORT = 8080

# Fake mode only. Safe because fake mode already refuses to run anywhere a real
# PBX is configured or reachable, so this password can never guard the booth.
FAKE_CONSOLE_PASSWORD = "fake-pbx"

WEB_DIST = REPO_ROOT / "web" / "dist"

# How long to wait for the ARI port before deciding nothing is there. Local
# connect either resolves in microseconds or is not going to.
ARI_PROBE_TIMEOUT_S = 0.25


class FakeModeRefused(RuntimeError):
    """Fake mode was requested alongside a configured real ARI connection."""


class ConsoleRefused(RuntimeError):
    """The Console was asked to serve with no shared password to gate it."""


@dataclass(frozen=True)
class Runtime:
    """A wired-up run: the engine, what it is talking to, and (in fake mode)
    the harness driving it and the sandbox it lives in."""

    engine: CallEngine
    ari: ARIClient | FakePBX
    harness: FakePBXHarness | None = None
    workspace: FakeWorkspace | None = None
    console: ConsoleServer | None = None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the entry point's arguments; fake-only flags require ``--fake-pbx``."""
    parser = argparse.ArgumentParser(
        prog="python -m engine",
        description="Run the Call Engine against Asterisk, or against the Fake PBX.",
    )
    parser.add_argument(
        "--fake-pbx",
        action="store_true",
        help="DEVELOPMENT ONLY: drive the engine with synthetic calls instead of Asterisk.",
    )
    parser.add_argument(
        "--no-console",
        action="store_true",
        help="Run the phone without serving the Operator Console.",
    )
    parser.add_argument(
        "--fake-interval",
        type=float,
        default=None,
        help=f"Seconds of quiet between synthetic calls (default: {DEFAULT_INTERVAL_S}).",
    )
    parser.add_argument(
        "--fake-cycles",
        type=int,
        default=None,
        help="Times to run the scenario matrix, or 0 to keep going (default: 0).",
    )
    parser.add_argument(
        "--fake-audio-wav",
        action="store_true",
        default=None,
        help="Record the synthetic Listen-in audio to a WAV in the fake workspace.",
    )
    args = parser.parse_args(argv)

    # The fake-only flags default to None rather than their real defaults, so
    # passing one is detectable even when the value happens to equal the
    # default — "--fake-cycles 0" without --fake-pbx is still a mistake.
    if not args.fake_pbx:
        for flag in ("fake_interval", "fake_cycles", "fake_audio_wav"):
            if getattr(args, flag) is not None:
                parser.error(f"--{flag.replace('_', '-')} requires --fake-pbx")
    args.fake_interval = DEFAULT_INTERVAL_S if args.fake_interval is None else args.fake_interval
    args.fake_cycles = 0 if args.fake_cycles is None else args.fake_cycles
    args.fake_audio_wav = bool(args.fake_audio_wav)
    return args


def ari_is_listening(base_url: str, timeout_s: float = ARI_PROBE_TIMEOUT_S) -> bool:
    """Is something accepting connections on ``base_url``'s host and port?

    The env check below catches an ARI connection someone configured on
    purpose; this catches the one they didn't — running the harness on the Mac
    Mini, where Asterisk is up and the defaults would have reached it.
    """
    parsed = urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def fake_workspace_root(env: Mapping[str, str]) -> Path:
    """Where a fake-mode run keeps its sandbox.

    Under ``data/`` by default, which is gitignored — synthetic history and the
    config the harness rewrites should never be committable.
    """
    return Path(env.get("PIZZA_FAKE_PBX_DIR", str(REPO_ROOT / "data" / "fake-pbx")))


def console_password(env: Mapping[str, str], config_dir: Path) -> str | None:
    """The Console's shared password, or ``None`` if nobody has set one.

    The environment first (systemd unit, shell), then ``config/console.json``.
    Blank is treated as absent, not as an empty password — otherwise a config
    file with the key present and the value forgotten would let the whole LAN
    in by submitting nothing.
    """
    from_env = env.get("PIZZA_CONSOLE_PASSWORD", "").strip()
    if from_env:
        return from_env

    path = config_dir / CONSOLE_CONFIG_FILENAME
    try:
        data = read_raw(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.exception("Cannot read the Console password from %s", path)
        return None

    return str(data.get("password", "")).strip() or None


def build_console(
    args: argparse.Namespace,
    engine: CallEngine,
    *,
    config_path: Path,
    env: Mapping[str, str],
    fallback_password: str | None = None,
) -> ConsoleServer | None:
    """Wire the Console the engine serves, unless this run doesn't want one.

    ``config_path`` is the Global Config the Console reports — the booth's, or
    in fake mode the sandbox's, so what the Operator sees is what the synthetic
    calls are actually running.
    """
    if args.no_console:
        return None

    password = console_password(env, config_path.parent)
    if password is None and fallback_password is not None:
        password = fallback_password
        logger.warning(
            "No Console password set; using the fake-mode development password %r", password
        )
    if not password:
        raise ConsoleRefused(
            "The Operator Console has no shared password, and an unguarded console "
            "hands the booth's Code to anyone on the network. Set "
            "PIZZA_CONSOLE_PASSWORD, or put {\"password\": \"...\"} in "
            f"{config_path.parent / CONSOLE_CONFIG_FILENAME} — or run with --no-console."
        )

    return ConsoleServer(
        engine,
        password=password,
        config_path=config_path,
        dist_dir=WEB_DIST,
        host=env.get("PIZZA_CONSOLE_HOST", DEFAULT_CONSOLE_HOST),
        port=int(env.get("PIZZA_CONSOLE_PORT", DEFAULT_CONSOLE_PORT)),
    )


def build_runtime(
    args: argparse.Namespace, env: Mapping[str, str] | None = None
) -> Runtime:
    """Wire the run described by ``args``, against Asterisk or the Fake PBX."""
    env = os.environ if env is None else env
    if args.fake_pbx:
        return _build_fake_runtime(args, env)
    return _build_real_runtime(args, env)


def _build_real_runtime(args: argparse.Namespace, env: Mapping[str, str]) -> Runtime:
    ari = ARIClient(
        base_url=env.get("ARI_BASE_URL", DEFAULT_ARI_BASE_URL),
        username=env.get("ARI_USERNAME", "pizza"),
        password=env.get("ARI_PASSWORD", "pizza"),
        app=env.get("ARI_APP", "pizza-phone"),
    )
    db_path = Path(env.get("PIZZA_DB_PATH", str(REPO_ROOT / "data" / "calls.db")))
    config_dir = REPO_ROOT / "config"
    engine = CallEngine(
        ari,
        CallStore(db_path),
        config_dir=config_dir,
        log_dir=REPO_ROOT / "logs",
        audio_dir=REPO_ROOT / "audio",
    )
    console = build_console(args, engine, config_path=config_dir / CONFIG_FILENAME, env=env)
    return Runtime(engine=engine, ari=ari, console=console)


def _build_fake_runtime(args: argparse.Namespace, env: Mapping[str, str]) -> Runtime:
    configured = [name for name in ARI_ENV_VARS if env.get(name)]
    if configured:
        raise FakeModeRefused(
            "--fake-pbx cannot run alongside a configured ARI connection "
            f"({', '.join(configured)} set). The Fake PBX is development-only: "
            "unset those variables, or drop --fake-pbx to run against Asterisk."
        )
    if ari_is_listening(DEFAULT_ARI_BASE_URL):
        raise FakeModeRefused(
            f"--fake-pbx refuses to run with ARI reachable at {DEFAULT_ARI_BASE_URL}. "
            "The Fake PBX is development-only and this machine looks like the rig: "
            "run the engine without --fake-pbx, or stop Asterisk first."
        )

    workspace = prepare_workspace(fake_workspace_root(env))
    if env.get("PIZZA_DB_PATH"):
        logger.warning(
            "Ignoring PIZZA_DB_PATH in fake mode; synthetic calls are recorded in %s",
            workspace.db_path,
        )
    harness = build_fake_harness(workspace, interval_s=args.fake_interval)
    console = build_console(
        args,
        harness.engine,
        config_path=workspace.config_path,
        env=env,
        # Nothing here guards anything real: the sandbox is synthetic and fake
        # mode refuses to run within reach of a live PBX.
        fallback_password=FAKE_CONSOLE_PASSWORD,
    )
    return Runtime(
        engine=harness.engine,
        ari=harness.pbx,
        harness=harness,
        workspace=workspace,
        console=console,
    )


async def _run(args: argparse.Namespace) -> None:
    runtime = build_runtime(args)
    tee = _attach_audio_tee(runtime) if args.fake_audio_wav else None

    await runtime.engine.start()
    if runtime.console is not None:
        await runtime.console.start()
    try:
        if runtime.harness is not None:
            logger.info("Fake PBX mode — no Asterisk attached; Ctrl-C to stop")
            await runtime.harness.run(cycles=args.fake_cycles)
        else:
            logger.info("Call engine running — Ctrl-C to stop")
            # Idle here while the engine services calls on its own tasks;
            # nothing else drives the loop until a signal cancels this wait.
            await asyncio.Event().wait()
    finally:
        if runtime.console is not None:
            await runtime.console.stop()
        await runtime.engine.aclose()
        if tee is not None:
            tee.close()
            logger.info("Synthetic Listen-in audio written to %s", tee.path)


def _attach_audio_tee(runtime: Runtime) -> WavTee:
    """Record the fake's Listen-in frames so a human can play them back."""
    assert runtime.harness is not None and runtime.workspace is not None
    tee = WavTee(runtime.workspace.listen_in_dir / "fake-pbx.wav")
    runtime.harness.pbx.subscribe_audio(tee)
    return tee


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run(args))
    except (FakeModeRefused, ConsoleRefused) as exc:
        raise SystemExit(f"error: {exc}") from exc
    except KeyboardInterrupt:
        logger.info("Interrupted; shutting down")


if __name__ == "__main__":
    main()
