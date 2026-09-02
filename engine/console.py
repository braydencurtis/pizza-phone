"""The Operator Console's server: the engine's second face, over HTTP (#35).

One process, one port. The Call Engine owns live calls; this module lets the
Operator *watch* them: it serves the built Console bundle, gates it behind the
shared password, and pushes whole-state snapshots to every connected browser
over a telemetry WebSocket.

Three things are deliberate here:

**Whole-state snapshots, never deltas.** Every push carries the full console
state (see ``engine/snapshot.py``), so a browser that connects late, misses a
message, or reconnects is immediately correct with no client-side reducer to
drift out of sync. Any number of browsers may be open — the room, not one
laptop — so a snapshot is broadcast to all of them.

**One shared password, no identity.** Auth is a single password exchanged for an
opaque session token held in engine memory: a restart logs everyone out, and
operators are indistinguishable by construction (CONTEXT.md, "Console
clients"). The token gates both the page load and the WebSocket upgrade, since a
socket someone can open without the cookie makes the login decorative.

**Global Config is read fresh, per snapshot.** The Console shows what the booth
is set to *now* — not what the live call is being judged against, which is that
call's frozen Config Snapshot. Reading the file each time is what makes an
Operator's write (`tools/rotate.py` today, the Console in #38) show up here
without the engine holding a cached copy that can go stale.

**The socket is never silent, and the session is askable** (#40). The browser
owns reconnection; this side owes it two affordances to do that honestly — a
keepalive snapshot, so silence on the wire means a dead connection rather than a
quiet booth, and ``/api/session``, which answers the question a refused
WebSocket upgrade cannot. See ``engine/README.md``, "Reconnection".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import secrets
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from aiohttp import WSCloseCode, WSMsgType, web

from core.config import take_snapshot
from engine.call_session import CallSession
from engine.snapshot import build_snapshot

logger = logging.getLogger(__name__)

SESSION_COOKIE = "pizza_console"

# Long enough to outlast an event: nobody should be typing the password again
# because a caller took until midnight. Tokens live in memory anyway, so a
# restart is the real end of a session.
SESSION_TTL = timedelta(hours=12)

TELEMETRY_PATH = "/ws/telemetry"

# How often a snapshot goes out regardless of whether anything changed (#40).
# The Console treats a longer silence than this as a dead socket, so the pulse
# is what makes "nothing is happening" distinguishable from "nothing is there".
# Being a whole-state snapshot like any other, it also repairs a browser that
# somehow missed one. Cheap at this size: the console's entire state is a few
# hundred bytes, a handful of times a minute, to a handful of laptops.
KEEPALIVE = timedelta(seconds=20)

# How long one browser may take to accept a snapshot before it is dropped.
# Sends go out concurrently, but a socket whose TCP window has closed — a
# laptop asleep or carried out of range — would still hold the broadcast lock,
# and with it everyone else's view of the live call, for as long as it liked.
BROADCAST_TIMEOUT_S = 5.0

# The three types the built bundle is made of, spelled out rather than left to
# the platform: a Mac and a Debian box disagree about ``.js``, and a browser
# handed ``text/plain`` for a module script refuses to run it. Anything else
# Vite emits later is guessed, like any other static file.
CONTENT_TYPES = {
    ".css": "text/css",
    ".html": "text/html",
    ".js": "application/javascript",
}


class ConsoleEngine(Protocol):
    """What the Console needs from the engine, and nothing more.

    The read side of the engine as the Console sees it: the live call (or
    ``None`` when idle), plus a hook that fires whenever that changes. Keeping
    it this narrow is what lets the server's tests drive a fake engine — and
    what keeps the engine unaware of HTTP.
    """

    active_session: CallSession | None

    def on_change(self, callback: Callable[[], None]) -> Callable[[], None]: ...


def password_matches(offered: str, expected: str) -> bool:
    """Is ``offered`` the shared password? Compared in constant time.

    ``compare_digest`` rather than ``==`` so the comparison doesn't leak the
    password's length or its matching prefix through how long the reply takes.
    """
    return secrets.compare_digest(offered.encode(), expected.encode())


class SessionStore:
    """The logged-in browsers, as opaque tokens with an expiry.

    In memory on purpose: an engine restart logs everyone out, which is the
    behaviour we want from a booth that gets power-cycled, and it means no
    session state to persist or invalidate. Expired tokens are dropped lazily,
    on the next lookup — a handful of operators is not a leak worth a sweeper
    task.
    """

    def __init__(self, ttl: timedelta = SESSION_TTL) -> None:
        self._ttl = ttl
        self._expiry: dict[str, datetime] = {}

    def create(self) -> tuple[str, datetime]:
        """Mint a session token and return it with the moment it dies."""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + self._ttl
        self._expiry[token] = expires_at
        return token, expires_at

    def validate(self, token: str | None) -> bool:
        """Is this a live session? Expired tokens are forgotten as we find them."""
        if not token:
            return False
        expires_at = self._expiry.get(token)
        if expires_at is None:
            return False
        if expires_at <= datetime.now(UTC):
            del self._expiry[token]
            return False
        return True

    def revoke(self, token: str | None) -> None:
        """Drop a session (logout). Unknown tokens are not an error."""
        if token:
            self._expiry.pop(token, None)

    @property
    def max_age_seconds(self) -> int:
        return int(self._ttl.total_seconds())


class ConsoleServer:
    """Serves the Operator Console: login, the bundle, and the telemetry socket.

    Runs in the engine's own event loop and process. Construct it with the
    engine to observe, the shared password, the Global Config file to report,
    and the built bundle to serve; :meth:`start` binds the port and subscribes
    to the engine, :meth:`stop` releases both.
    """

    def __init__(
        self,
        engine: ConsoleEngine,
        *,
        password: str,
        config_path: Path,
        dist_dir: Path,
        host: str,
        port: int,
        session_ttl: timedelta = SESSION_TTL,
        keepalive: timedelta = KEEPALIVE,
        broadcast_timeout_s: float = BROADCAST_TIMEOUT_S,
    ) -> None:
        self._engine = engine
        self._password = password
        self._config_path = config_path
        self._dist_dir = Path(dist_dir)
        self._host = host
        self._requested_port = port
        self._sessions = SessionStore(ttl=session_ttl)
        self._keepalive = keepalive
        self._broadcast_timeout_s = broadcast_timeout_s

        self._app = self._build_app()
        self._runner: web.AppRunner | None = None
        self._bound_port: int | None = None
        self._sockets: set[web.WebSocketResponse] = set()
        # Background work — broadcasts, and closing sockets we have given up
        # on — is spawned from synchronous callers, so the tasks need an owner
        # or the loop may garbage-collect one mid-flight.
        self._tasks: set[asyncio.Task[None]] = set()
        # One broadcast at a time, so two changes in quick succession reach
        # every browser in the order they happened.
        self._broadcast_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._pulse: asyncio.Task[None] | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def config_path(self) -> Path:
        """The Global Config file this Console reports (the booth's, or a sandbox's)."""
        return self._config_path

    @property
    def port(self) -> int:
        """The port actually bound. Only meaningful after :meth:`start`."""
        if self._bound_port is None:
            raise RuntimeError("ConsoleServer is not started")
        return self._bound_port

    async def start(self) -> None:
        """Bind the port and start pushing snapshots on engine changes."""
        self._loop = asyncio.get_running_loop()
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._requested_port)
        await site.start()
        self._bound_port = _bound_port(self._runner)
        self._unsubscribe = self._engine.on_change(self._on_engine_change)
        self._pulse = asyncio.create_task(self._keep_alive())
        logger.info("Operator Console on http://%s:%d", self._host, self._bound_port)

    async def stop(self) -> None:
        """Stop listening, close every open socket, release the port.

        Safe to call twice. Unsubscribing matters as much as the port: a
        stopped server left wired to the engine is a listener nobody can see,
        broadcasting into closed sockets for the life of the process.
        """
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._pulse is not None:
            self._pulse.cancel()
            self._pulse = None
        for ws in list(self._sockets):
            with contextlib.suppress(Exception):
                await ws.close()
        self._sockets.clear()
        for task in list(self._tasks):
            task.cancel()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._bound_port = None

    # -- routing -----------------------------------------------------------

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/login", self._handle_login)
        app.router.add_post("/api/logout", self._handle_logout)
        app.router.add_get("/api/session", self._handle_session)
        app.router.add_get(TELEMETRY_PATH, self._handle_telemetry)
        app.router.add_get("/login", self._handle_login_page)
        app.router.add_get("/assets/{path:.*}", self._handle_asset)
        # The machine surfaces stop at the paths named above. Without these two
        # the catch-all below would answer a mistyped endpoint with a page of
        # HTML, and the caller would fail somewhere downstream in a JSON parse
        # rather than at the request that was wrong.
        app.router.add_route("*", "/api/{tail:.*}", self._handle_unknown_endpoint)
        app.router.add_route("*", "/ws/{tail:.*}", self._handle_unknown_endpoint)
        # Everything else is the Console itself: one bundle, client-side
        # routing, so an unknown path is a deep link rather than a 404.
        app.router.add_get("/{tail:.*}", self._handle_console_page)
        return app

    async def _handle_unknown_endpoint(self, request: web.Request) -> web.Response:
        return web.json_response({"error": "no such endpoint"}, status=404)

    # -- auth --------------------------------------------------------------

    def _authenticated(self, request: web.Request) -> bool:
        return self._sessions.validate(request.cookies.get(SESSION_COOKIE))

    async def _handle_login(self, request: web.Request) -> web.Response:
        """Exchange the shared password for a session cookie.

        A body that isn't the expected JSON is treated as a failed login, not a
        400: from here it is indistinguishable from a wrong guess, and there is
        nothing useful to tell whoever sent it.
        """
        offered = ""
        with contextlib.suppress(Exception):
            payload = await request.json()
            if isinstance(payload, dict):
                offered = str(payload.get("password", ""))

        if not password_matches(offered, self._password):
            logger.warning("Console login refused from %s", request.remote)
            return web.json_response({"error": "invalid password"}, status=401)

        token, _ = self._sessions.create()
        response = web.json_response({"authenticated": True})
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=self._sessions.max_age_seconds,
            httponly=True,
            samesite="Lax",
            path="/",
        )
        logger.info("Console login from %s", request.remote)
        return response

    async def _handle_session(self, request: web.Request) -> web.Response:
        """Is this browser still logged in? The reconnection probe (#40).

        A browser whose telemetry socket will not open cannot tell from the
        WebSocket API *why*: a refused upgrade and an engine that is not
        listening both arrive as a bare close. The two want opposite responses —
        an engine restart has forgotten every Console Session, so the Operator
        needs the password box, while an engine that is merely down wants
        patience — so the browser asks here, where the status code can say.
        """
        if not self._authenticated(request):
            return web.json_response({"authenticated": False}, status=401)
        return web.json_response({"authenticated": True})

    async def _handle_logout(self, request: web.Request) -> web.Response:
        self._sessions.revoke(request.cookies.get(SESSION_COOKIE))
        response = web.json_response({"authenticated": False})
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    # -- static bundle -----------------------------------------------------

    async def _handle_login_page(self, request: web.Request) -> web.StreamResponse:
        """The password prompt: the one page a stranger is allowed to see."""
        return self._file_response(self._dist_dir / "login.html")

    async def _handle_console_page(self, request: web.Request) -> web.StreamResponse:
        """The Console, for a browser that has a session; the login page if not.

        The refusal stays a 401 — the page is genuinely not authorized, and a
        redirect would make an unauthenticated fetch look like it worked — but
        the *body* is the password prompt, so an Operator who opens the console
        cold gets somewhere to type instead of a JSON error.
        """
        if not self._authenticated(request):
            return self._file_response(self._dist_dir / "login.html", status=401)
        return self._file_response(self._dist_dir / "index.html")

    async def _handle_asset(self, request: web.Request) -> web.StreamResponse:
        """Serve one built asset.

        Public: the bundle is JS and CSS, and the browser has to fetch it
        *before* it can offer a login form. Nothing secret is in it — the
        secrets are behind ``/api``.
        """
        asset = _asset_path(self._dist_dir / "assets", request.match_info["path"])
        if asset is None:
            raise web.HTTPNotFound
        return self._file_response(asset)

    def _file_response(self, path: Path, status: int = 200) -> web.StreamResponse:
        """Serve a file from the built bundle, with its type spelled out.

        A missing file here means the bundle was never built (or never
        committed), which is an operator problem rather than a bad request —
        hence 503 with something to act on, not a bare 404.
        """
        if not path.is_file():
            logger.error("Console bundle missing: %s", path)
            return web.json_response(
                {
                    "error": "console bundle not built",
                    "detail": f"{path} is missing; run 'npm run build' in web/ and commit web/dist",
                },
                status=503,
            )
        return web.FileResponse(path, status=status, headers={"Content-Type": _content_type(path)})

    # -- telemetry ---------------------------------------------------------

    async def _handle_telemetry(self, request: web.Request) -> web.StreamResponse:
        """The telemetry socket: a snapshot on connect, then one per change."""
        if not self._authenticated(request):
            # Refused at the handshake, so an unauthenticated client never gets
            # a socket at all rather than one that is opened and then closed.
            return web.json_response({"error": "authentication required"}, status=401)

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._sockets.add(ws)
        logger.info("Console attached (%d watching)", len(self._sockets))
        try:
            snapshot = await self._snapshot()
            if snapshot is None:
                await ws.close(code=WSCloseCode.INTERNAL_ERROR, message=b"config unreadable")
                return ws
            await ws.send_json(snapshot)
            # Phase 2 is read-only: nothing the browser says means anything
            # yet, but the receive loop is what keeps the socket alive and
            # notices the close.
            async for message in ws:
                if message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            self._sockets.discard(ws)
            logger.info("Console detached (%d watching)", len(self._sockets))
        return ws

    async def _keep_alive(self) -> None:
        """Re-send the current snapshot on a timer, so silence means something.

        The engine otherwise only speaks when something changes, and a browser
        cannot hear the difference between a booth nobody is calling and a
        socket that died without closing — which is exactly what a laptop
        waking from sleep leaves behind. Skipped when nobody is watching.
        """
        interval = self._keepalive.total_seconds()
        while True:
            await asyncio.sleep(interval)
            if self._sockets:
                await self._broadcast()

    def _on_engine_change(self) -> None:
        """Engine state moved: push a fresh snapshot to everyone watching.

        Called synchronously by the engine — possibly from a worker thread — so
        it does no work itself beyond handing the loop a task.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._spawn_broadcast)

    def _spawn_broadcast(self) -> None:
        self._spawn(self._broadcast())

    async def _broadcast(self) -> None:
        async with self._broadcast_lock:
            if not self._sockets:
                return
            snapshot = await self._snapshot()
            if snapshot is None:
                return
            await self._send_to_all(snapshot)

    async def _send_to_all(self, snapshot: dict[str, Any]) -> None:
        """Snapshot to every browser at once, and nobody waiting on anybody.

        Concurrently and under a deadline, because the Console is a room full of
        laptops: one that has closed, gone to sleep or been carried out of range
        must not hold up everyone else's view of the live call. A browser that
        fails or dawdles is dropped here and closed in the background — the
        alternative is waiting on its close handshake, which is the stall we
        just refused to have.
        """
        await asyncio.gather(*(self._send_one(ws, snapshot) for ws in list(self._sockets)))

    async def _send_one(self, ws: web.WebSocketResponse, snapshot: dict[str, Any]) -> None:
        try:
            # TimeoutError included: it is an Exception, and a browser too slow
            # to take a snapshot is dropped for the same reason as one that
            # errored. Cancellation is a BaseException and passes through.
            await asyncio.wait_for(ws.send_json(snapshot), self._broadcast_timeout_s)
        except Exception:
            logger.debug("Dropping a console socket that failed mid-send", exc_info=True)
            self._sockets.discard(ws)
            self._spawn(_close_quietly(ws))

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        """Run a coroutine in the background, holding a reference to its task.

        Without an owner the loop may garbage-collect a task mid-flight.
        """
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _snapshot(self) -> dict[str, Any] | None:
        """The current console state, or ``None`` if Global Config won't read.

        Config is read off the loop (it is a file, and the loop is servicing a
        live call). An unreadable config is logged and skipped rather than
        raised: a malformed ``mode.json`` shouldn't take the socket — or the
        engine — down with it.
        """
        try:
            config = await asyncio.to_thread(take_snapshot, self._config_path)
        except (OSError, ValueError):
            logger.exception("Cannot read Global Config at %s", self._config_path)
            return None
        return build_snapshot(config, self._engine.active_session)


async def _close_quietly(ws: web.WebSocketResponse) -> None:
    """Close a socket we have already given up on, without raising about it."""
    with contextlib.suppress(Exception):
        await ws.close()


def _bound_port(runner: web.AppRunner) -> int:
    """The port the site actually got — the real one when 0 was requested."""
    _host, port, *_ = runner.addresses[0]
    return int(port)


def _asset_path(assets_dir: Path, relative: str) -> Path | None:
    """Resolve one asset request under ``assets_dir``, or ``None`` if it escapes.

    A request may arrive with ``..`` in it (percent-encoded, so the client
    hands it over intact), and ``dist/assets`` sits next to real files —
    ``config/mode.json`` holds the Code. So the resolved path is checked to be
    inside the assets directory before anything is opened.
    """
    if not relative or relative.startswith("/"):
        return None
    candidate = (assets_dir / relative).resolve()
    root = assets_dir.resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in CONTENT_TYPES:
        return CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
