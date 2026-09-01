# pizza-phone

A backrooms-themed phone booth experience. Callers solve puzzles to earn a 4-digit code that rings an upstairs phone — signaling the operator to drop pizza via dumbwaiter.

## Hardware

| Item | Role |
|------|------|
| Grandstream HT814 | Analog-to-SIP gateway for the booth phone |
| Yealink T46G | "Upstairs phone" that rings on success |
| Mac Mini (Debian) | Runs Asterisk PBX |

## Directory Structure

| Directory | Purpose |
|-----------|---------|
| `asterisk/` | Dialplan configs, SIP registrations, PBX settings |
| `core/` | Channel-agnostic game logic (modes, router, TTS) behind the `CallIO` seam |
| `engine/` | The ARI Call Engine — one asyncio process that owns live calls via ARI/Stasis |
| `config/` | Runtime config (mode, code, feature flags) |
| `logs/` | Call session logs (JSON lines, gitignored) |
| `tools/` | Operator/dev CLIs (code rotation + Slack notice, roguelike playground) |
| `scripts/` | Ops scripts (deploy, status) |

## Modes

- **Tweeted** — Code posted publicly; dial it to succeed
- **Audio Puzzle** — Listen to a riddle; answer is the code
- **Roguelike Phone-Tree** — Navigate a DTMF maze to discover the code

## Development

Set up once — a virtualenv with the runtime deps, the test suite, the type
checker and the linter:

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

The install is editable, so `core`, `engine` and `tools` import from the
working tree with no `PYTHONPATH` juggling. The three checks a change is
expected to pass:

```
.venv/bin/pytest
.venv/bin/mypy core engine tools tests
.venv/bin/ruff check .
```

Run the Call Engine against Asterisk:

```
python -m engine
```

The same process serves the **Operator Console** on port 8080, gated by one
shared password: put it in `config/console.json` (copy
`config/console.json.example` — the real file is gitignored) or set
`PIZZA_CONSOLE_PASSWORD`. With neither set the engine refuses to start rather
than serve the booth's Code to the network; `--no-console` is the deliberate way
to run just the phone.

The Console's frontend lives in [`web/`](./web/README.md) — React + Vite, with
`web/dist` committed so the booth host needs no Node toolchain. Change anything
under `web/src` and rebuild in the same commit:

```
cd web && npm install && npm run build
```

Or, away from the booth, against the **Fake PBX** — synthetic calls driving the
real engine, so the Operator Console can be built and demonstrated with no
hardware attached (development only; see [engine/README.md](./engine/README.md)):

```
python -m engine --fake-pbx
```

See [CONTEXT.md](./CONTEXT.md) for glossary and architectural decisions.
See [backrooms-phone-brief.md](./backrooms-phone-brief.md) for the project brief.
