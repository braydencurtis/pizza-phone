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
- `call_session.py` — `CallSession`: the engine's live in-memory state for the
  single active call. Carries the call's identity from `StasisStart` and the
  Config Snapshot it picked up with, is filled in with the terminal outcome, and
  flattens into a `CallRecord`.
- `call_engine.py` — `CallEngine`: the Phase 1 skeleton. Owns the ARI event
  loop; on each `StasisStart` runs one Call Session end-to-end and persists it.
- `console.py` — `ConsoleServer`: the Operator Console's server. Serves the
  built bundle from `web/dist`, gates it behind the shared password, and pushes
  whole-state snapshots to every attached browser over `/ws/telemetry`.
- `snapshot.py` — `build_snapshot`: the one place that knows the console's wire
  shape. Global Config plus the live Call Session, or an explicit idle marker,
  under a `schema` version.
- `fake_pbx.py` — the **Fake PBX**: a scripted stand-in for Asterisk that fires
  synthetic Call Sessions at the real engine. Development only (see below).
- `fake_audio.py` — the Fake PBX's media path: synthetic 8 kHz slin frames for a
  Listen-in consumer, plus the WAV tee. Development only.
- `__main__.py` — `python -m engine`: the dev/office run. Wires the engine from
  the environment (ARI connection) and the repo layout, then runs until Ctrl-C.
  `--fake-pbx` swaps Asterisk for the Fake PBX.

## The engine skeleton

`CallEngine` (#19) is the single asyncio process that owns live calls. On each
`StasisStart` it takes the call's Config Snapshot (`core/config.py`), answers
the channel, and dispatches to that snapshot's mode — tweeted / puzzle /
roguelike — through the `core.flow` handler behind the `ARICallIO` seam: the
same handlers, unchanged, that the retired AGI driver ran. The snapshot is what
that call is judged against for its whole duration, so an Operator rotating the
Code mid-call affects the next caller, never the one on the line (#34). On the
terminal outcome the flow routes the caller (success → the
`pizza-success` context; otherwise hangup), and the engine persists the
completed `CallSession` to the `CallStore`.

**One call at a time.** The booth has a single phone, so the engine holds one
`active_session`; a second `StasisStart` while a call is live is hung up rather
than queued.

**The handler stays non-blocking.** `ARIClient` dispatches event handlers inline
on its WebSocket reader task, so `StasisStart` handling must return promptly —
otherwise the reader could never deliver the DTMF and `PlaybackFinished` events
the call itself depends on. The handler therefore only claims the slot and
spawns a background task; that task runs the synchronous `core.flow` handler in
a worker thread (`asyncio.to_thread`) while the loop stays free to service
events — exactly the bridge `ARICallIO` is built around.

**Phase 2 seam.** `active_session` is the shared in-memory state: the Console
server slots into this same process and reads it directly to render the live
call. Persistence and the read side share one process, one in-memory state.
`on_change` is the other half of that seam — the engine announces that the live
state moved (slot claimed, Mode stamped at pickup, call over) and knows nothing
about who is listening or what they do with it.

## The Operator Console

One process, one port (#35). `ConsoleServer` runs in the engine's own event loop
and serves three things:

- **The bundle.** `web/dist`, committed, so a deploy is `git pull` + restart.
  `/login` and `/assets/*` are public — a browser must be able to fetch the JS
  that draws the password box. Every other path is the Console, and an
  unauthenticated page request answers *401 with the login page* rather than
  JSON: honest status, useful body. A missing bundle is a 503 that names the
  build step, not a 404.
- **Login.** One shared password (`config/console.json` or
  `PIZZA_CONSOLE_PASSWORD`), compared with `secrets.compare_digest`, exchanged
  for an opaque token held in memory — so a restart logs everyone out, and
  operators are indistinguishable by construction. The cookie gates the page
  *and* the socket upgrade; a socket anyone could open would make the login
  decorative.
- **Telemetry.** `/ws/telemetry` sends a snapshot on connect and one per engine
  change, broadcast to every attached browser (the room, not one laptop).
  Snapshots are **whole state**, never deltas, so a browser that connects late
  or misses a message is immediately correct and there is no reducer to drift.
  Global Config is re-read per snapshot: the Console shows what the booth is set
  to *now*, which is not necessarily what the live call is being judged against
  — that is the call's frozen Config Snapshot.

Broadcasts are serialized behind a lock so two changes reach every browser in
the order they happened, a browser that dies mid-send is dropped rather than
stalling the rest, and an unreadable `mode.json` is logged and skipped rather
than taking the socket down.

Reconnection is #40, so a dropped socket currently says so and stops — the
Console distinguishes an idle booth from a lost engine, which is the one thing a
dashboard must never blur.

## The Fake PBX

Phase 2 is built at the kitchen table, not in the booth. `fake_pbx.py` is what
makes that possible: a stand-in for Asterisk that speaks the same surface
`ARIClient` does, fires synthetic Call Sessions at the real `CallEngine` on a
timer, and emits synthetic call audio. Everything below Asterisk is genuine —
the real mode handlers run through the real `CallIO` seam, and the completed
sessions land in a real `CallStore`. Only the PBX is pretend.

```
python -m engine --fake-pbx                      # synthetic calls, forever
python -m engine --fake-pbx --fake-cycles 1      # one pass of the matrix, then exit
python -m engine --fake-pbx --fake-audio-wav     # …and record the Listen-in audio
```

`FakePBX` is the structural fake the engine tests were already written against,
promoted out of `tests/`. The harness and `tests/test_call_engine.py` drive that
same fake, so the thing a dev runs all day is the thing the suite covers.

**The scenario matrix.** `DEFAULT_SCENARIOS` walks every Mode and every terminal
outcome a caller can reach: dial the Code and win, burn the Attempt Limit and be
Exiled, pick up and say nothing. Before each call the harness writes that
scenario's Global Config (atomically — the engine is reading it at pickup), then
places the call and reads the persisted `CallRecord` back to check the session
ended where the scenario said it would. A run that starts logging warnings is
the signal that a change broke a Mode.

**Pacing.** The fake dawdles over `play` and `read_digits` (`Pacing`, default
`LIFELIKE`) so a synthetic call takes about as long as a real one and the
Console has something to watch. Tests run it at `INSTANT`.

**Synthetic audio.** `SyntheticAudioStream` emits 20 ms slin16 frames at 8 kHz —
the frame ARI ExternalMedia delivers — in wall-clock time, for as long as the
call lasts. A Listen-in consumer subscribes with `FakePBX.subscribe_audio`,
which is how the browser half of Listen-in gets built with no hardware; only
Snoop → ExternalMedia needs the rig. Following ADR-0003, the media path is spun
up **on demand**: with nobody listening the fake generates nothing, and a
consumer that attaches mid-call hears the rest of it. Each call steps to a new
tone so a new call is audible as one. `--fake-audio-wav` records the frames to a
playable WAV — until the Console can play them, that is how a human checks the
fake's audio is audio.

**It cannot be reached by accident.** Fake mode rewrites Global Config between
calls and fills the call store with invented history, so: the `--fake-pbx` flag
is the only switch (no environment variable, no config key, never a default);
the engine refuses to start if fake mode is requested while a real ARI
connection is configured (any `ARI_*` variable set); and the run is sandboxed in
its own workspace — config, logs, puzzle pool and database under
`PIZZA_FAKE_PBX_DIR` (default `data/fake-pbx`, gitignored). The booth's
`config/mode.json` and call store are never touched.

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
async with ARIClient("http://pbx:8088", user, secret, "pizza-phone") as ari:
    ari.on(STASIS_START, on_call)          # register/answer new calls
    await ari.answer(channel_id)
    await ari.play(channel_id, "sound:riddle")
    digits = await ari.read_digits(channel_id, num_digits=4, inter_digit_timeout_ms=5000)
```

Requires Asterisk's `http.conf` and `ari.conf` enabled with an ARI
user/password (Asterisk config is issue #15).
