# engine

The Call Engine: one long-running asyncio process that owns live calls via
ARI/Stasis and (later) serves the Operator Console. Replaces the per-call
blocking AGI scripts. See ADR-0001 and epic #13.

## Modules

- `ari_client.py` — `ARIClient`: a thin async ARI client. REST commands via
  `aiohttp`, events via `websockets`. Subscribes to `StasisStart`,
  `ChannelDtmfReceived`, `StasisEnd`, `ChannelHangupRequest`, and
  `PlaybackFinished`; offers `answer` / `play` / `read_digits` (DTMF
  accumulation) / `set_channel_var` / `continue_in_dialplan` / `hangup`.
  Snoop/bridge/record helpers are out of scope until Phases 2–3.
- `ari_call_io.py` — `ARICallIO`: adapts the async `ARIClient` to the
  synchronous `core.CallIO` protocol. This is where the sync→async shift is
  absorbed (see below).
- `call_store.py` — `CallStore`: the SQLite call-history store. Supersedes the
  JSONL logger (`core/logger.py`). A `CallRecord` per completed Call Session in
  a single-file `calls` table, queryable by mode/outcome/date. Stdlib `sqlite3`
  run off the event loop via `asyncio.to_thread` (see below). The `recording_*`
  columns hold WAV paths and stay empty until Phase 3.

The `CallSession` dispatch that writes to the store (#19) and the dashboard
WS/HTTP server that reads from it (Phase 2) land in later issues.

## The call-history store

`CallStore` is the queryable record of every completed Call Session, replacing
the JSONL logger. The console's needs are query-shaped — filter by
mode/outcome/date, paginate, later join calls to their recordings and hunt
clips for video — so history lives in a single-file SQLite database
(`CONTEXT.md`, "Call logging / persistence").

Access is stdlib `sqlite3`, not `aiosqlite`: a booth logs a handful of calls a
day, so the dependency would buy nothing. To keep blocking file I/O off the
engine's event loop, each public method (`initialize` / `add` / `get` /
`query`) is a coroutine that runs its query in a worker thread via
`asyncio.to_thread`, opening a fresh connection per call so nothing is shared
across threads. (Consequence: an in-memory `:memory:` database won't work —
each operation would open an empty DB; use a file path.) A `CallRecord`
round-trips through the twelve-column `calls` table with datetimes stored as
ISO 8601 UTC text (so date-range filters are plain string comparisons) and the
per-mode `detail` extras as JSON.

## The ARI CallIO adapter

`core.flow` is synchronous — it calls `play` / `read_dtmf` and blocks for the
result — while ARI is asynchronous, delivering playback completion and DTMF as
events on the engine's event loop. `ARICallIO` bridges the two: the engine runs
each mode handler in a worker thread (`asyncio.to_thread`), and every `CallIO`
method submits its ARI coroutine back to the loop with
`asyncio.run_coroutine_threadsafe(...).result()`, blocking the worker thread
until it resolves. The loop stays free to service events, so `play` unblocks on
`PlaybackFinished` and `read_dtmf` on the collected digits — the same blocking
shape the AGI verbs had. The mapping: `play`→`play`, `read_dtmf`→`read_digits`,
`speak`→TTS synth then `play` (as a `sound:` URI), `hangup`→`hangup`,
`to_success`→set `UPSTREAM_EXT` then `continue` into `pizza-success`.

Because of the bridge these methods must be called from a thread other than the
one running the loop; that is how the engine drives them.

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
