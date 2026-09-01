# web

The Operator Console's frontend: React + Vite, served by the Call Engine itself.

## Layout

- `index.html` / `src/main.tsx` — the Console (`App.tsx`, the cockpit).
- `login.html` / `src/login.tsx` — the password prompt. A separate page, not a
  route, because it is the one thing the engine will serve to a browser with no
  session.
- `src/snapshot.ts` — the wire shape, mirroring `engine/snapshot.py`.
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

The live-call panel is deliberately a stub: it says a call is in progress and
nothing more. The facts an Operator wants about a call — caller, elapsed time,
attempt and node as they happen — arrive with the state vocabulary in #36, and
belong in one shape with it.

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
