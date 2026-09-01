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
- `src/telemetry.ts` — the telemetry socket as a hook: the last snapshot, plus
  the state of the connection, kept deliberately separate.
- `src/api.ts` — the engine's HTTP surface (login / logout so far).

## Whole-state snapshots

The engine pushes the *entire* console state on every change — Global Config
plus the live Call Session, or an explicit `null` for idle — so there is no
reducer here to drift out of step with the engine, and a browser that connects
late is immediately correct. `SNAPSHOT_SCHEMA_VERSION` is checked against the
engine's, and a mismatch is shown rather than silently rendered.

An idle booth and a lost engine look nothing alike on screen: idle is a stated
fact from a live socket, "Engine lost" is the absence of one.

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
badly wrong system time will show a badly wrong elapsed.

## Dev

```
npm install
npm run dev        # Vite on :5173, proxying /api and /ws to the engine
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
