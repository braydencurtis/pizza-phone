"""A thin async ARI (Asterisk REST Interface) client.

Asterisk pushes channel events (``StasisStart``, DTMF, hangups, playback
completion) over a WebSocket, and the app issues commands over REST. This
module wraps exactly that: REST via :mod:`aiohttp`, events via
:mod:`websockets`. It is deliberately small — the sync→async shift and the
mapping onto ``core.CallIO`` live one layer up in the ARI adapter (#18); this
layer only speaks ARI.

Scope (Phase 1): connect, receive the events the game needs, and drive
``answer`` / ``play`` / accumulate-DTMF / ``continue`` / ``hangup``. Snoop,
bridge, and record helpers belong to Phases 2–3 and are intentionally absent.

The event surface is a small pub/sub (:meth:`ARIClient.on`) plus two await
helpers the adapter leans on: :meth:`ARIClient.play` waits for the matching
``PlaybackFinished``, and :meth:`ARIClient.read_digits` accumulates
``ChannelDtmfReceived`` digits with an inter-digit timeout — mirroring the
blocking ``read_digits`` the AGI driver offered.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlencode

import aiohttp
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

# The ARI events the game logic acts on. A Stasis app receives every event for
# its channels over one WebSocket, so there is nothing to subscribe to per-type;
# these names are what consumers pass to on() and what the client routes on.
STASIS_START = "StasisStart"
STASIS_END = "StasisEnd"
CHANNEL_DTMF_RECEIVED = "ChannelDtmfReceived"
CHANNEL_HANGUP_REQUEST = "ChannelHangupRequest"
PLAYBACK_FINISHED = "PlaybackFinished"

# An event handler receives the raw event dict; it may be sync or async.
EventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class ARIClient:
    """Thin async ARI client: REST commands + a WebSocket event stream.

    Typical use::

        async with ARIClient("http://pbx:8088", "user", "secret", "pizza") as ari:
            ari.on(STASIS_START, on_call)
            await ari.answer(channel_id)
            await ari.play(channel_id, "sound:hello")
            digits = await ari.read_digits(channel_id, num_digits=4, inter_digit_timeout_ms=5000)
    """

    def __init__(self, base_url: str, username: str, password: str, app: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._app = app

        self._session: aiohttp.ClientSession | None = None
        self._ws: ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None

        self._handlers: dict[str, list[EventHandler]] = {}
        # Per-channel DTMF buffers. Created on demand so digits that arrive
        # before read_digits() is called are not lost.
        self._dtmf_queues: dict[str, asyncio.Queue[str]] = {}
        # Per-playback completion flags. setdefault() on both the start and the
        # finish side means the wait resolves regardless of which happens first.
        self._playback_finished: dict[str, asyncio.Event] = {}

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Open the REST session and the event WebSocket, and start reading."""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                auth=aiohttp.BasicAuth(self._username, self._password)
            )
        self._ws = await ws_connect(self._ws_url())
        self._reader_task = asyncio.create_task(self._reader(), name="ari-event-reader")
        logger.info("ARI client connected to %s as app %r", self._base_url, self._app)

    async def close(self) -> None:
        """Stop reading events and release the WebSocket and (owned) session."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # -- event subscription ------------------------------------------------

    def on(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        """Register ``handler`` for ``event_type``; returns an unsubscribe callable."""
        self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    # -- REST helpers ------------------------------------------------------

    async def answer(self, channel_id: str) -> None:
        """Answer an inbound channel."""
        await self._request("POST", f"/channels/{channel_id}/answer")

    async def play(self, channel_id: str, media: str, *, timeout: float | None = None) -> None:
        """Play ``media`` and block until it finishes (mirrors AGI ``stream_file``)."""
        body = await self._request(
            "POST", f"/channels/{channel_id}/play", params={"media": media}
        )
        playback_id = str(body["id"])
        event = self._playback_finished.setdefault(playback_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout)
        finally:
            self._playback_finished.pop(playback_id, None)

    async def read_digits(
        self, channel_id: str, num_digits: int, inter_digit_timeout_ms: int
    ) -> str:
        """Accumulate DTMF digits, mirroring AGI ``read_digits``.

        Collects up to ``num_digits`` digits, resetting the inter-digit timer
        after each. Returns early — with whatever has been collected — when the
        timer lapses. Returns ``""`` if nothing is entered, which the flow
        treats as a hang-up signal.
        """
        queue = self._dtmf_queue(channel_id)
        timeout_s = inter_digit_timeout_ms / 1000
        digits: list[str] = []
        while len(digits) < num_digits:
            try:
                digit = await asyncio.wait_for(queue.get(), timeout_s)
            except TimeoutError:
                break
            digits.append(digit)
        return "".join(digits)

    async def continue_in_dialplan(
        self,
        channel_id: str,
        context: str | None = None,
        extension: str | None = None,
        priority: int | None = None,
    ) -> None:
        """Exit Stasis and continue the channel in the dialplan (e.g. success)."""
        params: dict[str, str] = {}
        if context is not None:
            params["context"] = context
        if extension is not None:
            params["extension"] = extension
        if priority is not None:
            params["priority"] = str(priority)
        await self._request(
            "POST", f"/channels/{channel_id}/continue", params=params or None
        )

    async def hangup(self, channel_id: str) -> None:
        """Tear down a channel."""
        await self._request("DELETE", f"/channels/{channel_id}")

    # -- internals ---------------------------------------------------------

    async def _request(
        self, method: str, path: str, *, params: dict[str, str] | None = None
    ) -> Any:
        if self._session is None:
            raise RuntimeError("ARIClient is not connected; call connect() first")
        url = f"{self._base_url}/ari{path}"
        async with self._session.request(method, url, params=params) as resp:
            resp.raise_for_status()
            if resp.content_type == "application/json":
                return await resp.json()
            return None

    async def _reader(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                text = message.decode() if isinstance(message, bytes) else message
                await self._handle_message(text)
        except ConnectionClosed:
            logger.info("ARI websocket closed")

    async def _handle_message(self, message: str) -> None:
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("Dropping non-JSON ARI message: %r", message)
            return
        if not isinstance(event, dict):
            logger.warning("Dropping non-object ARI event: %r", event)
            return

        event_type = event.get("type", "")
        if event_type == CHANNEL_DTMF_RECEIVED:
            channel_id = event.get("channel", {}).get("id", "")
            digit = event.get("digit", "")
            if channel_id and digit:
                self._dtmf_queue(channel_id).put_nowait(digit)
        elif event_type == PLAYBACK_FINISHED:
            playback_id = event.get("playback", {}).get("id", "")
            if playback_id:
                self._playback_finished.setdefault(playback_id, asyncio.Event()).set()
        elif event_type == STASIS_END:
            channel_id = event.get("channel", {}).get("id", "")
            if channel_id:
                # The call is over; drop its DTMF buffer so state doesn't grow
                # unbounded across the engine's lifetime.
                self._dtmf_queues.pop(channel_id, None)

        await self._dispatch(event_type, event)

    async def _dispatch(self, event_type: str, event: dict[str, Any]) -> None:
        for handler in list(self._handlers.get(event_type, ())):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("ARI event handler for %s failed", event_type)

    def _dtmf_queue(self, channel_id: str) -> asyncio.Queue[str]:
        queue = self._dtmf_queues.get(channel_id)
        if queue is None:
            queue = asyncio.Queue()
            self._dtmf_queues[channel_id] = queue
        return queue

    def _ws_url(self) -> str:
        base = self._base_url
        if base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        elif base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        query = urlencode({"app": self._app, "api_key": f"{self._username}:{self._password}"})
        return f"{base}/ari/events?{query}"
