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
  Config Snapshot it picked up with, tracks the Call State and the digits being
  dialled, is filled in with the terminal outcome, and flattens into a
  `CallRecord`.
- `call_engine.py` — `CallEngine`: the Phase 1 skeleton. Owns the ARI event
  loop; on each `StasisStart` runs one Call Session end-to-end and persists it.
- `call_observer.py` — `EngineCallObserver`: the engine's `core.CallObserver`.
  Carries the flow's live progress from the worker thread to the live session,
  on the event loop. See below.
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
state moved (slot claimed, Mode stamped at pickup, mode entered, a digit
dialled, call over) and knows nothing about who is listening or what they do
with it.

## The live Call Session (#36)

A call reaches the Console as a **Call State**: `answering` → `in_mode` → one
of `handed_off`, `exiled`, `hung_up` or `dropped`. The terminal states are held
apart on purpose.

- **`handed_off` is a win, not an ending.** The caller succeeded and the channel
  left the Call Engine for the success dialplan, so the Upstairs Phone ringing
  and everything after it is invisible from here. The Console says exactly that.
  If a win rendered like a hangup, the Operator would learn to distrust the
  panel.
- **`dropped` is the engine's fault, not the caller's** — `_handle_call` caught
  an exception and tore the channel down. Kept apart from `hung_up` for the same
  reason, and the engine works to keep them apart honestly: a caller who puts
  the handset down mid-playback makes every following ARI command 404, which
  would otherwise surface as "the engine dropped the call". So the engine
  subscribes to `StasisEnd` and `ChannelHangupRequest`, notes on the session
  that the caller left, and `CallSession.abandon()` picks `hung_up` or `dropped`
  from that.

Both are persisted (#50). The mode handler returns exactly once, at a tidy
outcome, so a call that ended any other way has no outcome of its own and
`abandon()` synthesises one: `hangup` for a caller who put the handset down —
the same ending as a caller who sat silent through a read, reaching us as a 404
instead of an empty string — and `dropped` for a failure of ours, an `Outcome`
no mode handler can return, so a broken call never lands in the hangup count.
The engine persists in a `finally`, so the tidy and the untidy endings converge
on one write. The single call not written is one that failed before its Config
Snapshot: no Mode means no game the caller can be recorded as having played, and
`to_record()` refuses it. Before this, a caller hanging up mid-playback left the
Console showing "hung up" and the store holding no row at all — the cockpit and
the history disagreeing about a call the Operator had just watched.

Everything the cockpit shows about a live call is observed from ARI events the
engine already receives, so `core/` is untouched: caller ID and start time come
from `StasisStart`, the Mode from the Config Snapshot taken at pickup, and the
dialled digits from `ChannelDtmfReceived` — the engine subscribes alongside
`ARIClient`'s own DTMF buffering and only *watches*; the digits are still
collected and judged by `ARICallIO`/`core.flow`. Digits for any channel but the
live one are ignored. The live digit display is a rolling window
(`MAX_LIVE_DIGITS`): the roguelike is an infinite maze, so the digit stream has
no natural end, and the whole call is in the store afterwards regardless.

Snapshots carry `started_at` — and `ended_at` once the call is over — never a
duration. The browser advances the clock (ADR-0003), so a call on the line does
not generate a message a second purely to tick a timer.

**The afterglow.** A finished call stays in `active_session` for `AFTERGLOW_S`
before the booth reads idle, so the Operator actually sees how it ended: a
terminal state that flickered past in a millisecond is a terminal state nobody
read, and because a broadcast builds its snapshot when it runs, the idle state
could otherwise overwrite the win before either was sent. It is display only —
`is_over` is what the busy check consults, so the next caller is accepted the
instant the call ends, afterglow or not.

## Live progress: the CallObserver seam (#37)

Everything in the previous section is observed from ARI events. Three things
are not, because they are computed inside `core.flow` and the flow returns
exactly once, at the terminal outcome: **which attempt of how many** the caller
is on, **which room of the maze** they are in, and **which riddle** they drew
from the Puzzle Pool. `core.CallObserver` is the way out, and
`EngineCallObserver` is this side of it.

**The session is only ever mutated on the event loop.** That is the invariant,
and the reason this class exists rather than the flow writing to the session
directly. `core.flow` runs in a worker thread; the Console reads the session on
the loop. Every other write — the digits from `ChannelDtmfReceived`, the state
changes, `complete()` — is already made on the loop, so keeping these there too
means the whole live state is single-threaded and a snapshot can never be built
from a session caught half-written: one moment's attempt count beside another
moment's node. The observer hands the loop a closure and returns immediately —
the mirror image of the hop `ARICallIO` makes in the other direction, except
that one *blocks* the worker on the result because the caller is waiting on a
prompt, and this one does not because nobody is waiting on a cockpit update.

**An observer belongs to one call and writes to no other.** The flow runs in a
thread the engine does not join, and `_handle_call`'s `finally:` frees the slot
without waiting for it — so a caller who hangs up while the flow sits in a
thirty-second `read_digits` leaves that thread alive and still emitting. By the
time the loop runs the closure the booth may have a different caller on the
line. So each observer holds the session it was made for and checks it is still
the one being shown before writing: the same identity check `_clear` makes
before dropping a finished call, and `_on_dtmf` makes on the channel id. Without
it, one caller's attempt count lands on the next caller's panel.

**Nothing here is worth a call.** These emissions sit directly in the path of a
live caller's attempt, so a closed loop (shutdown racing the last emission) and
a change listener that throws (a browser socket that died) are both logged and
swallowed rather than raised.

**Two Attempt Limits, and they are not the same number.** The snapshot's
`config.attempt_limit` is Global Config — what the booth is set to now. The
call's `attempt_limit` came off its frozen Config Snapshot and is what the
caller on the line is actually being judged against. They differ for the length
of any call in progress when an Operator changes the setting, and showing the
first on the call panel would tell the Operator a caller is one wrong answer
from Exile when they have three left.

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
- **Telemetry.** `/ws/telemetry` sends a snapshot on connect, one per engine
  change, and one every `KEEPALIVE` regardless, broadcast to every attached
  browser (the room, not one laptop). Snapshots are **whole state**, never
  deltas, so a browser that connects late or misses a message is immediately
  correct and there is no reducer to drift. Global Config is re-read per
  snapshot: the Console shows what the booth is set to *now*, which is not
  necessarily what the live call is being judged against — that is the call's
  frozen Config Snapshot.

Broadcasts are serialized behind a lock so two changes reach every browser in
the order they happened, and an unreadable `mode.json` is logged and skipped
rather than taking the socket down. Within a broadcast the sends go out
concurrently and under `BROADCAST_TIMEOUT_S`: a laptop that has been shut or
carried out of range holds an open socket whose TCP window never drains, and
without a deadline it would hold the lock — and with it the rest of the room's
view of the live call — for as long as it liked. A browser that fails or dawdles
is dropped and closed in the background.

## Reconnection (#40)

The browser owns recovery (`web/src/link.ts`); the engine owes it two things.

**The socket is never silent.** A snapshot goes out every `KEEPALIVE` even when
nothing has changed, because a connection killed by a sleeping laptop or a
vanished access point frequently never closes — and without a pulse the browser
cannot tell that from a booth nobody is calling, so it sits on a frozen screen
that looks fine. Being a whole-state snapshot like any other, the pulse also
repairs anything a browser managed to miss. It is skipped when nobody is
watching.

**The session is askable.** `GET /api/session` answers 200 or 401 for the
cookie presented. The WebSocket API reports a refused upgrade and an engine
that isn't listening identically — a bare close — but the Operator's next move
differs completely: Console Sessions live in engine memory, so a restart has
forgotten every one of them and the answer is the password box, whereas an
engine that is merely down wants patience. A browser whose socket closes without
ever having opened asks here before deciding which.

Neither is a new state to keep: the keepalive is a task started with the server
and cancelled with it, and the probe is the existing session check with a status
code on it.

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
Console has something to watch. Tests run it at `INSTANT`. A scripted entry is
*dialled* rather than just returned: each digit goes out as a
`ChannelDtmfReceived` first, spaced over the read, so the Console's live digit
display fills up a key at a time exactly as it will off a real handset.

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
