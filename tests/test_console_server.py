"""Tests for the Operator Console server (ticket #35).

The Call Engine serves the built console over its single HTTP port: a password
login that sets a session cookie, the static bundle, and a telemetry WebSocket
that broadcasts full state snapshots. These tests run a real ``ConsoleServer``
on an ephemeral port against a ``FakeEngine`` exposing the same
``active_session`` / ``on_change`` seam the real engine gets in this ticket.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from core.config import write_config
from engine.call_session import CallSession
from engine.console import (
    BROADCAST_TIMEOUT_S,
    KEEPALIVE,
    SESSION_COOKIE,
    SESSION_TTL,
    ConsoleServer,
    SessionStore,
    password_matches,
)
from engine.snapshot import SNAPSHOT_SCHEMA_VERSION

PASSWORD = "hunter2"


class FakeEngine:
    """The console's view of the engine: one live session plus a change hook."""

    def __init__(self) -> None:
        self.active_session: CallSession | None = None
        self._callbacks: list[Callable[[], None]] = []

    def on_change(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    @property
    def listeners(self) -> int:
        return len(self._callbacks)

    def set_session(self, session: CallSession | None) -> None:
        self.active_session = session
        for callback in list(self._callbacks):
            callback()


def _session(mode: str = "tweeted") -> CallSession:
    return CallSession(
        session_id="sess-1",
        channel_id="chan-1",
        started_at=datetime(2026, 8, 27, 12, 30, tzinfo=UTC),
        caller_id="+15551234567",
        mode=mode,  # type: ignore[arg-type]
        state="in_mode",
        attempts=2,
    )


def _write_bundle(dist_dir: Path) -> None:
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>booth</html>\n")
    (dist_dir / "login.html").write_text("<html>login</html>\n")
    (dist_dir / "assets" / "app.js").write_text("console.log('booth')\n")


@dataclass
class _Fixture:
    server: ConsoleServer
    engine: FakeEngine
    config_path: Path

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.port}"


async def _console(
    tmp_path: Path,
    *,
    bundle: bool = True,
    ttl: timedelta = SESSION_TTL,
    keepalive: timedelta = KEEPALIVE,
    broadcast_timeout_s: float = BROADCAST_TIMEOUT_S,
) -> _Fixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "mode.json"
    write_config(
        config_path,
        {"mode": "tweeted", "code": "1234", "attempt_limit": 5, "upstream_extension": "300"},
    )
    dist_dir = tmp_path / "dist"
    if bundle:
        _write_bundle(dist_dir)
    engine = FakeEngine()
    server = ConsoleServer(
        engine,
        password=PASSWORD,
        config_path=config_path,
        dist_dir=dist_dir,
        host="127.0.0.1",
        port=0,
        session_ttl=ttl,
        keepalive=keepalive,
        broadcast_timeout_s=broadcast_timeout_s,
    )
    await server.start()
    return _Fixture(server=server, engine=engine, config_path=config_path)


def _client() -> aiohttp.ClientSession:
    """A browser stand-in that will actually keep the session cookie.

    aiohttp's cookie jar discards cookies set by an IP-address host unless it
    is built ``unsafe``, and the console is served over the LAN by address —
    so without this every request after login arrives anonymous.
    """
    return aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))


def _cookie_token(http: aiohttp.ClientSession) -> str | None:
    for cookie in http.cookie_jar:
        if cookie.key == SESSION_COOKIE:
            return cookie.value
    return None


async def _login(http: aiohttp.ClientSession, fix: _Fixture, password: str = PASSWORD) -> int:
    resp = await http.post(f"{fix.base_url}/api/login", json={"password": password})
    return resp.status


async def _next_snapshot(ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
    message = await asyncio.wait_for(ws.receive(), timeout=5)
    assert message.type == aiohttp.WSMsgType.TEXT
    return json.loads(message.data)


def test_password_matches() -> None:
    assert password_matches("hunter2", "hunter2")
    assert not password_matches("hunter3", "hunter2")
    assert not password_matches("", "hunter2")


def test_login_with_correct_password_sets_cookie(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                assert _cookie_token(http) is not None
                resp = await http.get(f"{fix.base_url}/")
                assert resp.status == 200
                assert "booth" in await resp.text()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_login_with_wrong_password_is_rejected(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix, password="wrong") == 401
                assert _cookie_token(http) is None
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_login_with_unreadable_body_is_rejected(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                resp = await http.post(f"{fix.base_url}/api/login", data=b"not json at all")
                assert resp.status == 401
                assert _cookie_token(http) is None
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_root_requires_authentication(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                resp = await http.get(f"{fix.base_url}/")
                assert resp.status == 401
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_an_unauthenticated_page_request_answers_with_the_login_page(tmp_path: Path) -> None:
    """A human who opens the console cold should see a password box, not JSON."""

    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                resp = await http.get(f"{fix.base_url}/")
                assert resp.status == 401
                assert "login" in await resp.text()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_login_page_is_public(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                resp = await http.get(f"{fix.base_url}/login")
                assert resp.status == 200
                assert "login" in await resp.text()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_assets_are_public_without_authentication(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                resp = await http.get(f"{fix.base_url}/assets/app.js")
                assert resp.status == 200
                assert resp.headers["Content-Type"].startswith("application/javascript")
                assert "booth" in await resp.text()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_asset_request_cannot_escape_the_assets_directory(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                # Encoded slash so the client sends it; the decoded path is
                # /assets/../mode.json — the config file living above dist/.
                resp = await http.get(f"{fix.base_url}/assets/..%2Fmode.json")
                assert resp.status != 200
                assert "1234" not in await resp.text()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_spa_fallback_serves_index_when_authenticated(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                resp = await http.get(f"{fix.base_url}/calls/42")
                assert resp.status == 200
                assert "booth" in await resp.text()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_spa_fallback_requires_authentication(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                resp = await http.get(f"{fix.base_url}/calls/42")
                assert resp.status == 401
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_an_unknown_api_path_is_a_404_not_the_console(tmp_path: Path) -> None:
    """A mistyped endpoint must not answer 200 with a page of HTML.

    The console is one bundle behind a catch-all, so without this an `/api`
    call to the wrong path would come back as the console itself and fail as a
    JSON parse error somewhere in the browser.
    """

    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                for path in ("/api/nope", "/ws/nope"):
                    resp = await http.get(f"{fix.base_url}{path}")
                    assert resp.status == 404, path
                    assert resp.content_type == "application/json", path
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_logout_revokes_the_session(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                token = _cookie_token(http)
                assert token is not None
                resp = await http.post(f"{fix.base_url}/api/logout")
                assert resp.status == 200
                # Re-presenting the old cookie still finds no session.
                resp = await http.get(f"{fix.base_url}/", cookies={SESSION_COOKIE: token})
                assert resp.status == 401
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_session_cookie_expires() -> None:
    async def run() -> None:
        store = SessionStore(ttl=timedelta(milliseconds=50))
        token, _ = store.create()
        assert store.validate(token)
        await asyncio.sleep(0.06)
        assert not store.validate(token)

    asyncio.run(run())


def test_ws_rejects_an_unauthenticated_client(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                with pytest.raises(aiohttp.WSServerHandshakeError) as excinfo:
                    await http.ws_connect(f"{fix.base_url}/ws/telemetry")
                assert excinfo.value.status == 401
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_ws_sends_the_current_snapshot_on_connect(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                ws = await http.ws_connect(f"{fix.base_url}/ws/telemetry")
                try:
                    snap = await _next_snapshot(ws)
                    assert snap["schema"] == SNAPSHOT_SCHEMA_VERSION
                    assert snap["config"] == {
                        "mode": "tweeted",
                        "code": "1234",
                        "attempt_limit": 5,
                        "upstream_extension": "300",
                    }
                    assert snap["call"] is None
                finally:
                    await ws.close()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_ws_broadcasts_state_changes(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                ws = await http.ws_connect(f"{fix.base_url}/ws/telemetry")
                try:
                    snap = await _next_snapshot(ws)
                    assert snap["call"] is None
                    fix.engine.set_session(_session())
                    snap = await _next_snapshot(ws)
                    assert snap["call"]["session_id"] == "sess-1"
                    assert snap["call"]["mode"] == "tweeted"
                    assert snap["call"]["attempts"] == 2
                    assert snap["call"]["outcome"] is None
                    fix.engine.set_session(None)
                    snap = await _next_snapshot(ws)
                    assert snap["call"] is None
                finally:
                    await ws.close()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_ws_broadcasts_to_every_connected_client(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                ws_a = await http.ws_connect(f"{fix.base_url}/ws/telemetry")
                ws_b = await http.ws_connect(f"{fix.base_url}/ws/telemetry")
                try:
                    for ws in (ws_a, ws_b):
                        assert (await _next_snapshot(ws))["call"] is None
                    fix.engine.set_session(_session())
                    for ws in (ws_a, ws_b):
                        snap = await _next_snapshot(ws)
                        assert snap["call"]["session_id"] == "sess-1"
                finally:
                    await ws_a.close()
                    await ws_b.close()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_one_console_closing_does_not_disturb_the_others(tmp_path: Path) -> None:
    """Someone shuts their laptop; everyone else keeps watching the call."""

    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                ws_a = await http.ws_connect(f"{fix.base_url}/ws/telemetry")
                ws_b = await http.ws_connect(f"{fix.base_url}/ws/telemetry")
                try:
                    for ws in (ws_a, ws_b):
                        assert (await _next_snapshot(ws))["call"] is None
                    await ws_a.close()
                    fix.engine.set_session(_session())
                    snap = await _next_snapshot(ws_b)
                    assert snap["call"]["session_id"] == "sess-1"
                finally:
                    await ws_b.close()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_ws_snapshot_reflects_config_changes(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                ws = await http.ws_connect(f"{fix.base_url}/ws/telemetry")
                try:
                    await _next_snapshot(ws)
                    write_config(
                        fix.config_path, {"mode": "puzzle", "code": "9999", "attempt_limit": 1}
                    )
                    fix.engine.set_session(_session(mode="puzzle"))
                    snap = await _next_snapshot(ws)
                    assert snap["config"]["mode"] == "puzzle"
                    assert snap["config"]["code"] == "9999"
                    assert snap["config"]["attempt_limit"] == 1
                finally:
                    await ws.close()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_a_missing_bundle_is_a_503(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path, bundle=False)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                resp = await http.get(f"{fix.base_url}/")
                assert resp.status == 503
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_stop_stops_listening_to_the_engine(tmp_path: Path) -> None:
    """A stopped console must not stay wired to a phone that keeps ringing."""

    async def run() -> int:
        fix = await _console(tmp_path)
        assert fix.engine.listeners == 1
        await fix.server.stop()
        # And the engine can still change without reaching a dead server.
        fix.engine.set_session(_session())
        return fix.engine.listeners

    assert asyncio.run(run()) == 0


def test_stop_releases_the_port_and_is_idempotent(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        port = fix.server.port
        await fix.server.stop()
        probe = socket.create_server(("127.0.0.1", port))
        probe.close()
        await fix.server.stop()

    asyncio.run(run())


def test_ws_carries_the_live_call_vocabulary_to_the_browser(tmp_path: Path) -> None:
    """The state, the dialled digits and the win, over the real socket (#36)."""

    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                ws = await http.ws_connect(f"{fix.base_url}/ws/telemetry")
                try:
                    await _next_snapshot(ws)  # the idle booth

                    session = _session()
                    for digit in "1234":
                        session.record_digit(digit)
                    fix.engine.set_session(session)
                    snap = await _next_snapshot(ws)
                    assert snap["call"]["state"] == "in_mode"
                    assert snap["call"]["digits"] == "1234"
                    assert snap["call"]["started_at"]
                    assert snap["call"]["ended_at"] is None

                    session.complete({"mode": "tweeted", "outcome": "succeed", "attempts": 1})
                    fix.engine.set_session(session)
                    snap = await _next_snapshot(ws)
                    assert snap["call"]["state"] == "handed_off"
                    assert snap["call"]["ended_at"]
                finally:
                    await ws.close()
        finally:
            await fix.server.stop()

    asyncio.run(run())


# -- reconnection and connection status (#40) --------------------------------
#
# The Console recovers on its own and is honest while it cannot. The browser
# half of that lives in `web/src/link.ts`; these are the two things the engine
# owes it — a way to tell "your session is gone" from "I am not here", and a
# socket that keeps saying something so silence means something.


def test_the_session_probe_confirms_a_live_session(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                resp = await http.get(f"{fix.base_url}/api/session")
                assert resp.status == 200
                assert await resp.json() == {"authenticated": True}
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_the_session_probe_refuses_a_browser_with_no_session(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path)
        try:
            async with _client() as http:
                resp = await http.get(f"{fix.base_url}/api/session")
                assert resp.status == 401
                assert resp.content_type == "application/json"
                assert (await resp.json())["authenticated"] is False
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_the_session_probe_is_how_a_console_learns_the_engine_restarted(tmp_path: Path) -> None:
    """The socket can only say "closed"; this is what tells the Operator why.

    A restart takes every Console Session with it (they live in memory on
    purpose), so the reconnecting browser's cookie is worthless — and it must
    land back at the password box rather than retrying a socket that will be
    refused forever. A refused *socket* upgrade is indistinguishable in the
    browser from an engine that is simply down, so the browser asks over HTTP.
    """

    async def run() -> None:
        before = await _console(tmp_path / "before")
        async with _client() as http:
            assert await _login(http, before) == 200
            token = _cookie_token(http)
            assert token is not None
        await before.server.stop()

        after = await _console(tmp_path / "after")
        try:
            async with _client() as http:
                http.cookie_jar.update_cookies({SESSION_COOKIE: token})
                # The socket can only say "refused"…
                with pytest.raises(aiohttp.WSServerHandshakeError) as excinfo:
                    await http.ws_connect(f"{after.base_url}/ws/telemetry")
                assert excinfo.value.status == 401
                # …and this is where the browser finds out it means "log in".
                resp = await http.get(f"{after.base_url}/api/session")
                assert resp.status == 401
                assert (await resp.json())["authenticated"] is False
        finally:
            await after.server.stop()

    asyncio.run(run())


def test_the_socket_keeps_saying_something_when_nothing_changes(tmp_path: Path) -> None:
    """Silence must mean a dead socket, so a quiet booth is never silent.

    A connection killed by sleep or a vanished access point often never fires a
    close event in the browser. The keepalive is what makes the absence of
    messages diagnostic — and, being a whole-state snapshot like any other, it
    also repairs anything a browser managed to miss.
    """

    async def run() -> None:
        fix = await _console(tmp_path, keepalive=timedelta(milliseconds=40))
        try:
            async with _client() as http:
                assert await _login(http, fix) == 200
                ws = await http.ws_connect(f"{fix.base_url}/ws/telemetry")
                try:
                    assert (await _next_snapshot(ws))["call"] is None
                    # No engine change at all — only the pulse.
                    for _ in range(2):
                        snap = await _next_snapshot(ws)
                        assert snap["schema"] == SNAPSHOT_SCHEMA_VERSION
                        assert snap["call"] is None
                finally:
                    await ws.close()
        finally:
            await fix.server.stop()

    asyncio.run(run())


def test_the_keepalive_stops_with_the_server(tmp_path: Path) -> None:
    async def run() -> None:
        fix = await _console(tmp_path, keepalive=timedelta(milliseconds=20))
        await fix.server.stop()
        await asyncio.sleep(0.05)
        assert fix.server._pulse is None

    asyncio.run(run())


def test_a_console_that_dies_mid_send_does_not_hold_up_the_room(tmp_path: Path) -> None:
    """One wedged laptop must not stop everyone else seeing the call.

    Sends go out concurrently and under a deadline, so a browser whose TCP
    window has closed — asleep, or on a laptop carried out of range — is
    dropped rather than left blocking the broadcast the rest of the room is
    waiting on.
    """

    class _WedgedSocket:
        closed = False

        async def send_json(self, _snapshot: dict[str, Any]) -> None:
            await asyncio.sleep(60)

        async def close(self) -> None:
            self.closed = True

    class _HealthySocket:
        def __init__(self) -> None:
            self.received: list[dict[str, Any]] = []

        async def send_json(self, snapshot: dict[str, Any]) -> None:
            self.received.append(snapshot)

        async def close(self) -> None:  # pragma: no cover - never reached
            raise AssertionError("a healthy console should not be dropped")

    async def run() -> None:
        fix = await _console(tmp_path, broadcast_timeout_s=0.05)
        try:
            wedged = _WedgedSocket()
            healthy = _HealthySocket()
            fix.server._sockets.update({wedged, healthy})  # type: ignore[arg-type]

            await asyncio.wait_for(fix.server._broadcast(), timeout=2)

            assert len(healthy.received) == 1
            assert healthy.received[0]["call"] is None
            assert wedged not in fix.server._sockets
            assert healthy in fix.server._sockets
        finally:
            await fix.server.stop()

    asyncio.run(run())
