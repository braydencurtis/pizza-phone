# engine

The Call Engine: one long-running asyncio process that owns live calls via
ARI/Stasis and (later) serves the Operator Console. Replaces the per-call
blocking AGI scripts. See ADR-0001 and epic #13.

## Modules

- `ari_client.py` — `ARIClient`: a thin async ARI client. REST commands via
  `aiohttp`, events via `websockets`. Subscribes to `StasisStart`,
  `ChannelDtmfReceived`, `StasisEnd`, `ChannelHangupRequest`, and
  `PlaybackFinished`; offers `answer` / `play` / `read_digits` (DTMF
  accumulation) / `continue_in_dialplan` / `hangup`. Snoop/bridge/record
  helpers are out of scope until Phases 2–3.

The ARI→`core.CallIO` adapter (#18), the `CallSession` dispatch (#19), and the
dashboard WS/HTTP server land in later Phase-1/Phase-2 issues.

## The ARI client

`ARIClient` is intentionally small — it speaks ARI and nothing else. The
sync→async shift and the mapping onto `core.CallIO` live one layer up in the
adapter. Two await helpers absorb the event/command split the adapter needs:

- `play(channel, media)` starts a playback and blocks until its
  `PlaybackFinished` arrives (mirrors AGI `stream_file`).
- `read_digits(channel, num_digits, inter_digit_timeout_ms)` accumulates
  `ChannelDtmfReceived` digits, resetting the inter-digit timer after each and
  returning early on timeout — mirroring AGI `read_digits`. Digits are buffered
  per channel, so nothing is lost if they arrive before the read.

```python
async with ARIClient("http://pbx:8088", user, secret, "pizza") as ari:
    ari.on(STASIS_START, on_call)          # register/answer new calls
    await ari.answer(channel_id)
    await ari.play(channel_id, "sound:riddle")
    digits = await ari.read_digits(channel_id, num_digits=4, inter_digit_timeout_ms=5000)
```

Requires Asterisk's `http.conf` and `ari.conf` enabled with an ARI
user/password (Asterisk config is issue #15).
