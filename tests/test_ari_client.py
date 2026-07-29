from __future__ import annotations

import asyncio
import json
from typing import Any

from engine.ari_client import (
    CHANNEL_DTMF_RECEIVED,
    CHANNEL_HANGUP_REQUEST,
    PLAYBACK_FINISHED,
    STASIS_END,
    STASIS_START,
    ARIClient,
)


def _client(**kwargs: Any) -> ARIClient:
    return ARIClient("http://pbx:8088", "ariuser", "arisecret", "pizza", **kwargs)


class _RecordingRequest:
    """Stand-in for ARIClient._request that records calls and returns canned bodies."""

    def __init__(self, body: Any = None) -> None:
        self.body = body
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    async def __call__(
        self, method: str, path: str, *, params: dict[str, str] | None = None
    ) -> Any:
        self.calls.append((method, path, params))
        return self.body


def _dtmf_event(channel_id: str, digit: str) -> str:
    return json.dumps(
        {"type": CHANNEL_DTMF_RECEIVED, "channel": {"id": channel_id}, "digit": digit}
    )


# -- REST command mapping ------------------------------------------------------


def test_answer_posts_to_answer_endpoint() -> None:
    async def run() -> _RecordingRequest:
        client = _client()
        req = _RecordingRequest()
        client._request = req  # type: ignore[method-assign]
        await client.answer("chan-1")
        return req

    req = asyncio.run(run())
    assert req.calls == [("POST", "/channels/chan-1/answer", None)]


def test_continue_in_dialplan_only_sends_provided_params() -> None:
    async def run() -> _RecordingRequest:
        client = _client()
        req = _RecordingRequest()
        client._request = req  # type: ignore[method-assign]
        await client.continue_in_dialplan("chan-1", context="pizza-success", priority=1)
        return req

    req = asyncio.run(run())
    assert req.calls == [
        ("POST", "/channels/chan-1/continue", {"context": "pizza-success", "priority": "1"})
    ]


def test_set_channel_var_posts_variable_and_value() -> None:
    async def run() -> _RecordingRequest:
        client = _client()
        req = _RecordingRequest()
        client._request = req  # type: ignore[method-assign]
        await client.set_channel_var("chan-1", "UPSTREAM_EXT", "6001")
        return req

    req = asyncio.run(run())
    assert req.calls == [
        ("POST", "/channels/chan-1/variable", {"variable": "UPSTREAM_EXT", "value": "6001"})
    ]


def test_hangup_deletes_channel() -> None:
    async def run() -> _RecordingRequest:
        client = _client()
        req = _RecordingRequest()
        client._request = req  # type: ignore[method-assign]
        await client.hangup("chan-1")
        return req

    req = asyncio.run(run())
    assert req.calls == [("DELETE", "/channels/chan-1", None)]


# -- playback completion -------------------------------------------------------


def test_play_sends_media_then_blocks_until_playback_finished() -> None:
    async def run() -> _RecordingRequest:
        client = _client()
        req = _RecordingRequest(body={"id": "pb-1", "state": "queued"})
        client._request = req  # type: ignore[method-assign]

        play_task = asyncio.create_task(client.play("chan-1", "sound:riddle"))
        await asyncio.sleep(0)  # let play() issue the REST call and register the playback
        assert req.calls == [("POST", "/channels/chan-1/play", {"media": "sound:riddle"})]
        assert not play_task.done()

        await client._handle_message(
            json.dumps({"type": PLAYBACK_FINISHED, "playback": {"id": "pb-1"}})
        )
        await asyncio.wait_for(play_task, timeout=1)
        return req

    asyncio.run(run())


def test_play_resolves_even_if_finished_arrives_before_the_wait() -> None:
    # The finished event can race ahead of the wait; setdefault on both sides
    # means play() still resolves.
    async def run() -> None:
        client = _client()
        req = _RecordingRequest(body={"id": "pb-early"})
        client._request = req  # type: ignore[method-assign]
        await client._handle_message(
            json.dumps({"type": PLAYBACK_FINISHED, "playback": {"id": "pb-early"}})
        )
        await asyncio.wait_for(client.play("chan-1", "sound:x"), timeout=1)

    asyncio.run(run())


def test_play_gives_up_and_returns_when_finished_never_arrives() -> None:
    # A lost PlaybackFinished (e.g. caller hangs up mid-prompt) must not wedge
    # the call: play() returns on its timeout rather than raising or blocking.
    async def run() -> None:
        client = _client()
        req = _RecordingRequest(body={"id": "pb-stuck"})
        client._request = req  # type: ignore[method-assign]
        await asyncio.wait_for(client.play("chan-1", "sound:x", timeout=0.02), timeout=1)
        # The pending-playback flag is cleaned up even on the timeout path.
        assert client._playback_finished == {}

    asyncio.run(run())


# -- DTMF accumulation ---------------------------------------------------------


def test_read_digits_returns_when_num_digits_reached() -> None:
    async def run() -> str:
        client = _client()
        for digit in "1234":
            await client._handle_message(_dtmf_event("chan-1", digit))
        return await client.read_digits("chan-1", num_digits=4, inter_digit_timeout_ms=200)

    assert asyncio.run(run()) == "1234"


def test_read_digits_stops_at_num_digits_leaving_extra_buffered() -> None:
    async def run() -> tuple[str, str]:
        client = _client()
        for digit in "1234":
            await client._handle_message(_dtmf_event("chan-1", digit))
        first = await client.read_digits("chan-1", num_digits=2, inter_digit_timeout_ms=200)
        second = await client.read_digits("chan-1", num_digits=2, inter_digit_timeout_ms=200)
        return first, second

    assert asyncio.run(run()) == ("12", "34")


def test_read_digits_returns_partial_on_timeout() -> None:
    async def run() -> str:
        client = _client()
        await client._handle_message(_dtmf_event("chan-1", "9"))
        return await client.read_digits("chan-1", num_digits=4, inter_digit_timeout_ms=20)

    assert asyncio.run(run()) == "9"


def test_read_digits_returns_empty_when_no_input() -> None:
    async def run() -> str:
        client = _client()
        return await client.read_digits("chan-1", num_digits=4, inter_digit_timeout_ms=20)

    assert asyncio.run(run()) == ""


def test_read_digits_accumulates_digits_arriving_during_the_read() -> None:
    async def run() -> str:
        client = _client()

        async def feed() -> None:
            for digit in "1234":
                await asyncio.sleep(0.005)
                await client._handle_message(_dtmf_event("chan-1", digit))

        feeder = asyncio.create_task(feed())
        digits = await client.read_digits("chan-1", num_digits=4, inter_digit_timeout_ms=200)
        await feeder
        return digits

    assert asyncio.run(run()) == "1234"


def test_stasis_end_drops_the_channel_dtmf_buffer() -> None:
    async def run() -> bool:
        client = _client()
        await client._handle_message(_dtmf_event("chan-1", "1"))
        assert "chan-1" in client._dtmf_queues
        await client._handle_message(
            json.dumps({"type": STASIS_END, "channel": {"id": "chan-1"}})
        )
        return "chan-1" in client._dtmf_queues

    assert asyncio.run(run()) is False


def test_dtmf_queues_are_isolated_per_channel() -> None:
    async def run() -> tuple[str, str]:
        client = _client()
        await client._handle_message(_dtmf_event("chan-a", "1"))
        await client._handle_message(_dtmf_event("chan-b", "9"))
        a = await client.read_digits("chan-a", num_digits=1, inter_digit_timeout_ms=20)
        b = await client.read_digits("chan-b", num_digits=1, inter_digit_timeout_ms=20)
        return a, b

    assert asyncio.run(run()) == ("1", "9")


# -- event dispatch ------------------------------------------------------------


def test_on_delivers_events_to_handler() -> None:
    async def run() -> list[dict[str, Any]]:
        client = _client()
        seen: list[dict[str, Any]] = []
        client.on(STASIS_START, lambda event: seen.append(event))
        await client._handle_message(
            json.dumps({"type": STASIS_START, "channel": {"id": "chan-1"}})
        )
        return seen

    seen = asyncio.run(run())
    assert seen == [{"type": STASIS_START, "channel": {"id": "chan-1"}}]


def test_on_supports_async_handlers() -> None:
    async def run() -> list[str]:
        client = _client()
        seen: list[str] = []

        async def handler(event: dict[str, Any]) -> None:
            seen.append(event["type"])

        client.on(CHANNEL_HANGUP_REQUEST, handler)
        await client._handle_message(json.dumps({"type": CHANNEL_HANGUP_REQUEST}))
        return seen

    assert asyncio.run(run()) == [CHANNEL_HANGUP_REQUEST]


def test_unsubscribe_stops_further_delivery() -> None:
    async def run() -> int:
        client = _client()
        calls = 0

        def handler(event: dict[str, Any]) -> None:
            nonlocal calls
            calls += 1

        unsubscribe = client.on(STASIS_START, handler)
        await client._handle_message(json.dumps({"type": STASIS_START}))
        unsubscribe()
        await client._handle_message(json.dumps({"type": STASIS_START}))
        return calls

    assert asyncio.run(run()) == 1


def test_handler_exception_does_not_break_dispatch() -> None:
    async def run() -> list[str]:
        client = _client()
        seen: list[str] = []

        def boom(event: dict[str, Any]) -> None:
            raise ValueError("handler blew up")

        client.on(STASIS_START, boom)
        client.on(STASIS_START, lambda event: seen.append("ok"))
        await client._handle_message(json.dumps({"type": STASIS_START}))
        return seen

    assert asyncio.run(run()) == ["ok"]


def test_non_json_message_is_dropped() -> None:
    async def run() -> None:
        client = _client()
        # Should not raise.
        await client._handle_message("not json{")

    asyncio.run(run())


# -- URL construction ----------------------------------------------------------


def test_ws_url_carries_app_and_credentials() -> None:
    client = _client()
    url = client._ws_url()
    assert url.startswith("ws://pbx:8088/ari/events?")
    assert "app=pizza" in url
    assert "api_key=ariuser%3Aarisecret" in url


def test_ws_url_uses_wss_for_https_base() -> None:
    client = ARIClient("https://pbx:8089", "u", "p", "pizza")
    assert client._ws_url().startswith("wss://pbx:8089/ari/events?")


# -- reconnect -----------------------------------------------------------------


class _FakeWS:
    """A minimal event socket: yields its scripted messages, then either blocks
    open (``block=True``) or 'closes' by ending iteration."""

    def __init__(self, messages: list[str], *, block: bool = False) -> None:
        self._messages = messages
        self._block = block
        self.closed = False

    def __aiter__(self) -> Any:
        return self._iter()

    async def _iter(self) -> Any:
        for message in self._messages:
            yield message
        if self._block:
            await asyncio.Event().wait()  # stay open until cancelled

    async def close(self) -> None:
        self.closed = True


def test_reader_reconnects_after_the_socket_drops(monkeypatch: Any) -> None:
    """A dropped socket doesn't end the reader: it reconnects and keeps
    delivering events (here, the StasisStart that arrives on the second
    connection)."""

    async def run() -> list[str]:
        client = _client()
        seen: list[str] = []
        client.on(STASIS_START, lambda event: seen.append(event["channel"]["id"]))

        # First socket delivers chan-1 then ends (drop); the reconnect returns a
        # socket that delivers chan-2 then stays open so the reader parks.
        ws1 = _FakeWS([json.dumps({"type": STASIS_START, "channel": {"id": "chan-1"}})])
        ws2 = _FakeWS(
            [json.dumps({"type": STASIS_START, "channel": {"id": "chan-2"}})], block=True
        )
        reconnects = iter([ws2])

        async def fake_connect(_url: str) -> _FakeWS:
            return next(reconnects)

        monkeypatch.setattr("engine.ari_client.ws_connect", fake_connect)
        monkeypatch.setattr("engine.ari_client.RECONNECT_BASE_S", 0.01)

        client._ws = ws1  # type: ignore[assignment]  # structural stand-in for ClientConnection
        client._reader_task = asyncio.create_task(client._reader())
        for _ in range(200):
            if seen == ["chan-1", "chan-2"]:
                break
            await asyncio.sleep(0.01)
        await client.close()
        return seen

    assert asyncio.run(run()) == ["chan-1", "chan-2"]
