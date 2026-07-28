# core

Channel-agnostic game logic. `core/` never imports a channel driver (AGI, ARI);
it reaches the outside world only through the `CallIO` protocol, so the same
logic runs under the AGI scripts today and the ARI Call Engine next (epic #13).

## Modules

- `call_io.py` — `CallIO` protocol: the one seam a driver implements
  (`play`, `read_dtmf`, `speak`, `hangup`, `to_success`).
- `flow.py` — interactive per-mode Call Session flow (`run_tweeted`,
  `run_puzzle`, `run_roguelike`), driven entirely through `CallIO`.
- `router.py` — `Router.dispatch`: evaluate an attempt against the active mode
  and log the call-session record.
- `mode_tweeted.py` / `mode_puzzle.py` / `mode_roguelike.py` — per-mode logic;
  `mode_puzzle` also holds `PuzzleSelector`; `mode_roguelike` holds tree
  generation + navigation.
- `headless.py` — headless roguelike walker (used by the router and tooling).
- `tts.py` — text-to-speech backends (espeak / flite / macOS `say`).
- `code_manager.py` — read/write the daily code and mode in config.
- `logger.py` — `CallSessionLogger`: the JSON-lines call-session record.
- `types.py` — shared `Mode` / `Outcome` literals.
