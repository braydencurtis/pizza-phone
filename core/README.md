# core

Channel-agnostic game logic. `core/` never imports a channel driver (ARI, or the
retired AGI scripts); it reaches the outside world through two protocols and
nothing else: `CallIO` to talk to the **caller**, and `CallObserver` to tell the
**Operator** what is happening. The first is what let the same logic run under
the AGI scripts and now the ARI Call Engine (epic #13) — the driver changed,
this code did not.

## Modules

- `call_io.py` — `CallIO` protocol: the seam a driver implements to reach the
  caller (`play`, `read_dtmf`, `speak`, `hangup`, `to_success`).
- `observer.py` — `CallObserver` protocol: the seam a driver implements to reach
  the Operator (`attempt_started`, `node_entered`, `puzzle_selected`). Optional
  everywhere; defaults to `NULL_OBSERVER`.
- `flow.py` — interactive per-mode Call Session flow (`run_tweeted`,
  `run_puzzle`, `run_roguelike`), driven entirely through `CallIO`.
- `config.py` — Global Config: the `ConfigSnapshot` a Call Session is judged
  against, taken at pickup, plus the atomic `write_config` every Operator write
  goes through.
- `router.py` — `Router.dispatch`: evaluate an attempt against the session's
  Config Snapshot and log the call-session record.
- `mode_tweeted.py` / `mode_puzzle.py` / `mode_roguelike.py` — per-mode logic;
  `mode_puzzle` also holds `PuzzleSelector`; `mode_roguelike` holds tree
  generation + navigation, and returns a `Walk` — where the caller went and
  which of the two ways it ended.
- `headless.py` — headless roguelike walker (used by the router and tooling).
- `tts.py` — text-to-speech backends (espeak / flite / macOS `say`).
- `code_manager.py` — field-level edits to Global Config (rotate the code,
  switch the mode), atomic via `config.py`.
- `logger.py` — `CallSessionLogger`: the JSON-lines call-session record.
- `types.py` — shared `Mode` / `Outcome` / `WalkOutcome` literals.

## The Config Snapshot

Global Config — the active Mode, the Code, the Attempt Limit — is one JSON file
the Operator writes at any time. A Call Session does not read it as it goes: it
takes a `ConfigSnapshot` at pickup and is judged against that for its whole
duration, so a Code rotated mid-call lands on the *next* caller. `Router` holds
the snapshot and `core.flow` reads the Code and Attempt Limit off it, so the
digits collected and the digits judged always come from the same config. Writes
go through `config.write_config`, which replaces the file atomically — a call
taking its snapshot mid-write can never read a truncated file. See issue #34.

## Silence ends a call

An empty `read_dtmf` means the caller has gone, and every mode flow treats it
the same way: tear the call down, end the Call Session on `hangup`. It goes
through one function, `flow._caller_left`, so the rule is one rule rather than
three that happen to agree. This matters more than it looks — the Call Engine
owns one call at a time, so a mode that kept asking an empty booth would hold
the slot and hang up on every caller behind it. That is what the Roguelike
Phone-Tree did until #53: it read silence as an unrecognised key and replayed
the room, forever, and `max_depth` could not stop it because no move was ever
made.

A key that *is* pressed but is not a choice is still forgiven — the room is
replayed and nothing is counted against the caller — but only five times in a
row in one room, after which the walk ends on the same `hangup` (#55). That
bound is liveness, not lives: the maze has nothing to get wrong, so it is not
counting wrong answers, it is noticing that nobody is choosing. The count resets
on every key the caller does choose, so fumbling once in each of a dozen rooms
never approaches it, while a handset lying on a wedged key — the one thing
`max_depth` could no more stop than it could stop silence, since a refused key
makes no move — stops holding the booth.

The other way a caller leaves — putting the handset down while a prompt plays —
never reaches these functions at all: the channel dies, the next ARI command
404s, and the flow raises instead of returning. The engine ends that call on the
same `hangup` (#50), so one caller behaviour keeps one outcome however the news
arrives. `Outcome` also carries `dropped` for the engine's own failures, which
nothing in `core/` returns.

## The two seams

`CallIO` and `CallObserver` are deliberately separate, and ADR-0003 is the
argument for it. The cheap alternative was a `progress()` method on `CallIO`,
but its five methods are all *talk to the caller* while telemetry is *talk to
the Operator*; conflating two audiences on the seam whose smallness is the whole
reason this code outlived its driver would be a poor trade for one fewer file.

The observer exists because the `run_*` functions return exactly once, at the
terminal outcome. Everything the cockpit wants to show *during* a call is
computed inside them and otherwise never escapes: which attempt of how many
(`_run_code_entry`'s loop counter), which room of the maze
(`mode_roguelike.handle`), which riddle was drawn from the Puzzle Pool.

It stays optional — every flow function defaults to `NULL_OBSERVER`, a shared
do-nothing instance — so `core/` remains usable and testable with no observer at
all, and so the emission sites need no null check to forget at the next one.

Two obligations fall on whoever implements it, both because these calls sit
directly in the path of a live caller's attempt: **do not raise** (telemetry is
never worth a call) and **do not block** (the caller is on the phone). The
engine's implementation, `engine/call_observer.py`, also has a third: it is
called from the worker thread running the flow, so it marshals onto the event
loop rather than touching live state itself.
