# tools

Operator and developer CLIs. These sit outside the call path (`core/` +
`engine/`) — they read or mutate config and talk to humans, not live calls.

## Key Scripts

- `rotate.py` — Rotate the daily code/mode in `config/mode.json` and post the
  new code to Slack. Run from the repo root:

  ```sh
  python -m tools.rotate --mode puzzle --code 4242
  ```

  Reads `SLACK_WEBHOOK_URL` from the environment; skips the Slack notice if it
  is unset. Moved here from the retired `agi/` in Phase 1 (#20); the operator
  console will absorb rotation in Phase 2 (Eyes).
- `slack_notifier.py` — Slack webhook notifier used by `rotate.py`.
- `play_roguelike.py` — Interactive stdin/stdout playground for roguelike mode.
