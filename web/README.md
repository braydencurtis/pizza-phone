# web

The Operator Console's frontend: React + Vite, served by the Call Engine itself.

## Layout

- `index.html` / `src/main.tsx` — the Console (`App.tsx`, the cockpit).
- `login.html` / `src/login.tsx` — the password prompt. A separate page, not a
  route, because it is the one thing the engine will serve to a browser with no
  session.
- `src/snapshot.ts` — the wire shape, mirroring `engine/snapshot.py`, plus the
  labels the Call State vocabulary renders as.
- `src/elapsed.ts` — the elapsed-call clock, advanced in the browser.
- `src/link.ts` — the reconnecting telemetry link: the socket, kept up. Plain
  TypeScript with its socket, clock, timers and session probe injected, so the
  suite drives an afternoon of outages in milliseconds.
- `src/backoff.ts` — how long to wait before trying again.
- `src/telemetry.ts` — the React skin over the link, plus the browser signals it
  cannot see for itself (network back, tab refocused, laptop woken).
- `src/api.ts` — the engine's HTTP surface (login / logout / the session probe).

## Whole-state snapshots

The engine pushes the *entire* console state on every change — Global Config
plus the live Call Session, or an explicit `null` for idle — so there is no
reducer here to drift out of step with the engine, and a browser that connects
late is immediately correct. `SNAPSHOT_SCHEMA_VERSION` is checked against the
engine's, and a mismatch is shown rather than silently rendered.

An idle booth and a lost engine look nothing alike on screen: idle is a stated
fact from a live socket, "Engine lost" is the absence of one.

## Off Air

The failure being designed against is not a console that goes blank — it is one
that goes on looking fine while the engine is gone, so a broken night reads as a
quiet one. The moment contact drops, three things happen together: a red banner
says so and counts down to the next attempt, everything the last snapshot said
is dimmed and stamped *last seen*, and the elapsed clock stops dead. Nothing on
screen is left claiming to be current. The last snapshot is kept rather than
thrown away — an Operator wants to know what *was* happening — but it can only
be read as a memory.

Recovery is automatic and needs no resync protocol: snapshots are whole state,
so the first message after reattaching is the entire truth. Retries back off
exponentially with jitter (jitter because the room's several browsers all drop
at the same instant and must not come back in lockstep), capped at 15s so a
console left open still notices the engine return. Waking the laptop, refocusing
the tab or the network coming back all retry immediately — and replace a socket
that sleep killed without ever firing a close event.

Two engine-side affordances make that honest — a keepalive snapshot and
`GET /api/session`. Both are described in `engine/README.md`, "Reconnection".

In code Off Air is two flags, because they move at different moments:
`connection` (`lost`, then `connecting` while an attempt is in flight) and
`stale` (what is on screen is a memory). `stale` outlives the handshake — it
clears when a snapshot actually lands, not when the socket opens, or the panel
would un-dim and the clock restart on a call that ended minutes ago.

## The live call

The cockpit shows the caller's number, the Mode they are in, an elapsed timer
and the digits as they are dialled, under a headline that names the **Call
State**: answering → on the line → Handed Off / Exiled / hung up (or *dropped*,
when the engine ended the call itself).

Every terminal state gets its own colour and its own sentence. **Handed Off is a
win and reads like one** — green, and it says out loud that the channel has left
the Call Engine for the success dialplan, so nobody mistakes the silence that
follows for the story ending. A hangup is dashed and grey. A dashboard that
blurred those two would be a dashboard the Operator stops believing.

The attempt counter appears only once the call is over: the engine learns it
when the mode handler returns, so a live call would show a permanent `0` and
then jump. A live counter arrives with the `CallObserver` seam.

The timer is advanced here, not by the engine: snapshots carry the call's start
time (and its end, once it is over), so a live call costs one message per event
rather than one a second. The clock is therefore the viewer's — a browser with a
badly wrong system time will show a badly wrong elapsed. It also stops when
contact does: a counter still ticking on a dead socket is the most convincing
lie the screen can tell.

## Dev

```
npm install
npm run dev        # Vite on :5173, proxying /api and /ws to the engine
npm test           # vitest — the link's state machine, and the backoff
```

The engine is expected at `http://127.0.0.1:8080`; point elsewhere with
`PIZZA_ENGINE_URL`. Run it alongside with `python -m engine --fake-pbx` for
synthetic calls to watch.

## Build

```
npm run build      # typecheck, then emit dist/
```

**`dist/` is committed on purpose.** The engine serves it directly (one
process, one port) so the booth host needs no Node toolchain and a deploy stays
`git pull` + restart — see CONTEXT.md, "Web dashboard deployment". Rebuild and
commit `dist/` in the same commit as any change under `src/`.
