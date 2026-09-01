# Configuration

Runtime configuration files that control behavior without code changes.

## Key Files

- `mode.json` — **Global Config**: the active Mode, the current Code, the
  Attempt Limit, and the Upstairs Phone extension. Written by the Operator
  (`tools/rotate.py` today, the Console in #38) and snapshotted by each Call
  Session at pickup, so a write lands on the *next* caller and never the one on
  the line. Writes go through `core.config.write_config`, which replaces the
  file atomically. Committed, because the booth's defaults are repo content.
- `console.json` — the Operator Console's single shared password
  (`{"password": "..."}`). **Gitignored**; `console.json.example` is the
  committed template. `PIZZA_CONSOLE_PASSWORD` overrides it, which is what the
  systemd unit uses. With neither set the engine refuses to serve the Console
  rather than expose the Code to the LAN — `python -m engine --no-console` is
  the deliberate way to run without one.
- `prompts/` — Prompt library metadata (file paths, node definitions for the
  Roguelike tree).
