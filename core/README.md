# core

Channel-agnostic game logic. `core/` never imports a channel driver (ARI, or the
retired AGI scripts); it reaches the outside world only through the `CallIO`
protocol. That seam is what let the same logic run under the AGI scripts and now
the ARI Call Engine (epic #13) — the driver changed, this code did not.

## Modules

- `call_io.py` — `CallIO` protocol: the one seam a driver implements
  (`play`, `read_dtmf`, `speak`, `hangup`, `to_success`).
- `flow.py` — interactive per-mode Call Session flow (`run_tweeted`,
  `run_puzzle`, `run_roguelike`), driven entirely through `CallIO`.
- `config.py` — Global Config: the `ConfigSnapshot` a Call Session is judged
  against, taken at pickup, plus the atomic `write_config` every Operator write
  goes through.
- `router.py` — `Router.dispatch`: evaluate an attempt against the session's
  Config Snapshot and log the call-session record.
- `mode_tweeted.py` / `mode_puzzle.py` / `mode_roguelike.py` — per-mode logic;
  `mode_puzzle` also holds `PuzzleSelector`; `mode_roguelike` holds tree
  generation + navigation.
- `headless.py` — headless roguelike walker (used by the router and tooling).
- `tts.py` — text-to-speech backends (espeak / flite / macOS `say`).
- `code_manager.py` — field-level edits to Global Config (rotate the code,
  switch the mode), atomic via `config.py`.
- `logger.py` — `CallSessionLogger`: the JSON-lines call-session record.
- `types.py` — shared `Mode` / `Outcome` literals.

## The Config Snapshot

Global Config — the active Mode, the Code, the Attempt Limit — is one JSON file
the Operator writes at any time. A Call Session does not read it as it goes: it
takes a `ConfigSnapshot` at pickup and is judged against that for its whole
duration, so a Code rotated mid-call lands on the *next* caller. `Router` holds
the snapshot and `core.flow` reads the Code and Attempt Limit off it, so the
digits collected and the digits judged always come from the same config. Writes
go through `config.write_config`, which replaces the file atomically — a call
taking its snapshot mid-write can never read a truncated file. See issue #34.
