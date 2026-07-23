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
| `agi/` | Python AGI scripts for interactive call logic |
| `config/` | Runtime config (mode, code, feature flags) |
| `logs/` | Call session logs (JSON lines, gitignored) |
| `scripts/` | Utility scripts (code rotation, status) |

## Modes

- **Tweeted** — Code posted publicly; dial it to succeed
- **Audio Puzzle** — Listen to a riddle; answer is the code
- **Roguelike Phone-Tree** — Navigate a DTMF maze to discover the code

## Development

See [CONTEXT.md](./CONTEXT.md) for glossary and architectural decisions.
See [backrooms-phone-brief.md](./backrooms-phone-brief.md) for the project brief.
