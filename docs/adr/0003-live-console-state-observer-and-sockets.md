# Live console state: a `core` observer seam and two WebSockets

The Operator Console must render a call *as it happens* — mode, attempt count, roguelike node, digits dialled — but `core.flow` is synchronous, runs in a worker thread, and returns only once, at the terminal outcome. Nothing escapes it mid-call. We are adding a second seam in `core/` (a `CallObserver` protocol the flow functions notify at game-significant moments) and carrying its output to the browser as **full state snapshots** over a **telemetry WebSocket**, with **Listen-in audio on a separate socket**.

## Why

Splitting the telemetry strip by who knows what, most of it is already available: the engine learns call state, caller ID and duration from `StasisStart`, and live DTMF from `ChannelDtmfReceived`, both on the event loop. Only **attempt number** (`_run_code_entry`'s loop counter) and **roguelike node** live inside `core/` with no way out. So the seam only has to carry what genuinely can't be observed from the engine side.

Telemetry is a few sporadic JSON messages per call; Listen-in is ~16 KB/s of continuous binary `slin` for the call's duration. Sharing one socket puts the latency-critical, low-volume signal behind the high-volume one, and lets a stalled audio consumer in the browser back-pressure the connection that also carries "the caller just hung up."

At this message volume, deltas buy nothing and cost the classic divergence bug — a cockpit confidently displaying a stale attempt count. Snapshots are idempotent, so reconnection is "wait for the next one" rather than a resync protocol.

## Considered options

- **Widen `CallIO`** with a `progress()` method — cheapest, but `CallIO`'s five methods are all *talk to the caller*; telemetry is *talk to the operator*. Rejected: conflates two audiences on a seam whose smallness is the reason the same flow logic ran under both AGI and ARI.
- **Infer progress engine-side** by counting `read_dtmf` calls through `ARICallIO` — no `core/` change, but a read means "attempt" in code-entry modes and "node choice" in roguelike. Rejected as fragile and mode-dependent.
- **A `CallObserver` protocol in `core/`** (chosen) — keeps `core/` channel-agnostic, keeps `CallIO` pure caller-I/O, and is the read-only tracer bullet for Phase 3's "roguelike teleport," which needs the console to *know* the current node rather than guess it.
- **Incremental events over the socket** with a client-side reducer — rejected; see above.

## Consequences

- The observer is called from the worker thread running `core.flow`, so its engine-side implementation must hop to the event loop (`call_soon_threadsafe`) — the mirror image of the `run_coroutine_threadsafe` hop `ARICallIO` already makes. A naive implementation will race.
- Three `run_*` functions in `core/flow.py` gain an optional observer parameter. It stays optional so `core/` remains usable — and testable — with no observer at all.
- **This supersedes ADR-0002's description of Listen-in as travelling over "the dashboard WebSocket"** (singular). Audio gets its own socket, opened only while Listen-in is engaged; the Snoop/ExternalMedia machinery is therefore spun up on demand rather than per call.
- Snapshots carry `started_at`, not a duration — the browser advances the clock — so a live call doesn't generate a message per second purely to tick a timer.
- The snapshot is broadcast to all connected clients (the room may have several open). Commands are last-write-wins with no identity and no conflict resolution; a correlated `ack` tells the clicker whether their own write landed. With a single shared password the system cannot distinguish operators, so per-user attribution is deliberately out of scope.
