"""Tests for the engine entry point's wiring (#33).

Fake mode is the thing that must not misfire: it rewrites Global Config and
fills the call store with invented history, so it has to be impossible to reach
by accident and impossible to point at the booth. These tests pin the flag as
the only way in, the refusal when a real ARI connection is configured, and the
sandbox the harness runs inside.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest

from engine import __main__ as engine_main
from engine.__main__ import (
    CONSOLE_CONFIG_FILENAME,
    ConsoleRefused,
    FakeModeRefused,
    ari_is_listening,
    build_runtime,
    console_password,
    fake_workspace_root,
    parse_args,
)
from engine.ari_client import ARIClient
from engine.console import ConsoleServer
from engine.fake_pbx import DEFAULT_INTERVAL_S, INSTANT, FakePBX

REPO_ROOT = Path(__file__).resolve().parent.parent


# -- fake mode is opt-in, and only by the flag -------------------------------


def test_fake_mode_is_off_by_default() -> None:
    assert parse_args([]).fake_pbx is False


def test_without_the_flag_the_engine_talks_to_real_ari(tmp_path: Path) -> None:
    runtime = build_runtime(
        parse_args([]),
        env={"PIZZA_DB_PATH": str(tmp_path / "calls.db"), "PIZZA_CONSOLE_PASSWORD": "hunter2"},
    )
    assert isinstance(runtime.ari, ARIClient)
    assert runtime.harness is None
    assert runtime.workspace is None


def test_environment_cannot_enable_fake_mode(tmp_path: Path) -> None:
    """No env var is a back door — the flag is the only switch."""
    env = {
        "PIZZA_DB_PATH": str(tmp_path / "calls.db"),
        "PIZZA_CONSOLE_PASSWORD": "hunter2",
        "PIZZA_FAKE_PBX": "1",
        "FAKE_PBX": "true",
        "PIZZA_FAKE_PBX_DIR": str(tmp_path / "fake"),
    }
    runtime = build_runtime(parse_args([]), env=env)
    assert isinstance(runtime.ari, ARIClient)
    assert runtime.harness is None


def test_the_flag_builds_a_fake_pbx(tmp_path: Path) -> None:
    runtime = build_runtime(
        parse_args(["--fake-pbx"]), env={"PIZZA_FAKE_PBX_DIR": str(tmp_path / "fake")}
    )
    assert isinstance(runtime.ari, FakePBX)
    assert runtime.harness is not None
    assert runtime.workspace is not None


def test_fake_only_flags_require_fake_mode() -> None:
    for argv in (["--fake-interval", "1"], ["--fake-cycles", "2"], ["--fake-audio-wav"]):
        with pytest.raises(SystemExit):
            parse_args(argv)


def test_fake_only_flags_require_fake_mode_even_at_their_defaults() -> None:
    """Passing the default value is still passing the flag."""
    for argv in (["--fake-interval", str(DEFAULT_INTERVAL_S)], ["--fake-cycles", "0"]):
        with pytest.raises(SystemExit):
            parse_args(argv)


def test_fake_flags_resolve_to_their_defaults() -> None:
    args = parse_args(["--fake-pbx"])
    assert args.fake_interval == DEFAULT_INTERVAL_S
    assert args.fake_cycles == 0
    assert args.fake_audio_wav is False


# -- fake mode never runs against the booth ----------------------------------


@pytest.mark.parametrize(
    "variable", ["ARI_BASE_URL", "ARI_USERNAME", "ARI_PASSWORD", "ARI_APP"]
)
def test_fake_mode_refuses_a_configured_ari_connection(tmp_path: Path, variable: str) -> None:
    env = {variable: "pizza", "PIZZA_FAKE_PBX_DIR": str(tmp_path / "fake")}
    with pytest.raises(FakeModeRefused) as excinfo:
        build_runtime(parse_args(["--fake-pbx"]), env=env)
    assert variable in str(excinfo.value)


def test_ari_probe_sees_a_listening_port() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        assert ari_is_listening(f"http://127.0.0.1:{port}") is True
    # Closed again once the server is gone.
    assert ari_is_listening(f"http://127.0.0.1:{port}") is False


def test_fake_mode_refuses_a_reachable_ari(tmp_path: Path, monkeypatch) -> None:
    """Nothing configured, but Asterisk is answering — this is the rig."""
    monkeypatch.setattr(engine_main, "ari_is_listening", lambda *a, **kw: True)
    with pytest.raises(FakeModeRefused) as excinfo:
        build_runtime(
            parse_args(["--fake-pbx"]), env={"PIZZA_FAKE_PBX_DIR": str(tmp_path / "fake")}
        )
    assert "reachable" in str(excinfo.value)


def test_fake_mode_sandboxes_config_and_history(tmp_path: Path) -> None:
    """Synthetic calls touch neither the booth's config nor its call history."""
    live_db = tmp_path / "live" / "calls.db"
    live_config = (REPO_ROOT / "config" / "mode.json").read_text()

    runtime = build_runtime(
        parse_args(["--fake-pbx"]),
        env={
            "PIZZA_FAKE_PBX_DIR": str(tmp_path / "fake"),
            "PIZZA_DB_PATH": str(live_db),  # ignored in fake mode
        },
    )
    workspace = runtime.workspace
    assert workspace is not None
    assert workspace.root == tmp_path / "fake"

    async def run() -> None:
        assert runtime.harness is not None
        runtime.harness.pbx.pacing = INSTANT  # no need to wait out a lifelike call
        await runtime.engine.start()
        try:
            await runtime.harness.run_scenario(runtime.harness.scenarios[0])
        finally:
            await runtime.engine.aclose()

    asyncio.run(run())

    assert workspace.db_path.exists()  # synthetic history lands in the sandbox
    assert not live_db.exists()  # and nowhere near the real store
    assert (REPO_ROOT / "config" / "mode.json").read_text() == live_config
    assert json.loads(workspace.config_path.read_text())["mode"] in {
        "tweeted",
        "puzzle",
        "roguelike",
    }


def test_fake_workspace_defaults_under_the_repo_data_dir() -> None:
    # data/ is gitignored, so nothing synthetic can be committed by accident.
    assert fake_workspace_root({}).parent.name == "data"


def test_fake_workspace_can_be_relocated(tmp_path: Path) -> None:
    assert fake_workspace_root({"PIZZA_FAKE_PBX_DIR": str(tmp_path)}) == tmp_path


# -- the Operator Console the engine serves (#35) -----------------------------


def test_the_console_password_comes_from_the_environment(tmp_path: Path) -> None:
    assert console_password({"PIZZA_CONSOLE_PASSWORD": "hunter2"}, tmp_path) == "hunter2"


def test_the_console_password_falls_back_to_the_config_file(tmp_path: Path) -> None:
    (tmp_path / CONSOLE_CONFIG_FILENAME).write_text(json.dumps({"password": "from-file"}))
    assert console_password({}, tmp_path) == "from-file"


def test_the_environment_wins_over_the_config_file(tmp_path: Path) -> None:
    (tmp_path / CONSOLE_CONFIG_FILENAME).write_text(json.dumps({"password": "from-file"}))
    assert console_password({"PIZZA_CONSOLE_PASSWORD": "from-env"}, tmp_path) == "from-env"


def test_no_password_anywhere_is_no_password(tmp_path: Path) -> None:
    assert console_password({}, tmp_path) is None


def test_a_blank_password_is_not_a_password(tmp_path: Path) -> None:
    """An empty string would let anyone in by submitting nothing."""
    (tmp_path / CONSOLE_CONFIG_FILENAME).write_text(json.dumps({"password": "  "}))
    assert console_password({"PIZZA_CONSOLE_PASSWORD": ""}, tmp_path) is None


def test_the_engine_serves_the_console(tmp_path: Path) -> None:
    runtime = build_runtime(
        parse_args([]),
        env={
            "PIZZA_DB_PATH": str(tmp_path / "calls.db"),
            "PIZZA_CONSOLE_PASSWORD": "hunter2",
            "PIZZA_CONSOLE_PORT": "9123",
        },
    )
    assert isinstance(runtime.console, ConsoleServer)


def test_the_console_can_be_left_off(tmp_path: Path) -> None:
    """An escape hatch for a run that only wants the phone working."""
    runtime = build_runtime(
        parse_args(["--no-console"]),
        env={"PIZZA_DB_PATH": str(tmp_path / "calls.db")},
    )
    assert runtime.console is None


def test_the_console_refuses_to_serve_without_a_password(tmp_path: Path) -> None:
    with pytest.raises(ConsoleRefused) as excinfo:
        build_runtime(parse_args([]), env={"PIZZA_DB_PATH": str(tmp_path / "calls.db")})
    assert "PIZZA_CONSOLE_PASSWORD" in str(excinfo.value)


def test_fake_mode_serves_the_console_without_a_password(tmp_path: Path) -> None:
    """The kitchen table doesn't need a password ritual; the sandbox is fake."""
    runtime = build_runtime(
        parse_args(["--fake-pbx"]), env={"PIZZA_FAKE_PBX_DIR": str(tmp_path / "fake")}
    )
    assert isinstance(runtime.console, ConsoleServer)


def test_fake_mode_reports_the_sandbox_config_not_the_booths(tmp_path: Path) -> None:
    """The Console must show the config the fake calls are actually running."""
    runtime = build_runtime(
        parse_args(["--fake-pbx"]), env={"PIZZA_FAKE_PBX_DIR": str(tmp_path / "fake")}
    )
    assert runtime.console is not None
    assert runtime.workspace is not None
    assert runtime.console.config_path == runtime.workspace.config_path


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
