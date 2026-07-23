# Logs

Call session logs generated at runtime. Each line is a JSON object.

## Schema (JSON lines)

- `timestamp` — ISO 8601 start time
- `mode` — Active mode (tweeted, puzzle, roguelike)
- `outcome` — succeed, fail, exile
- `duration` — Call duration in seconds
- `attempts` — Number of code attempts made
- `path` — Sequence of DTMF choices (Roguelike mode only)

## Note

Log files are gitignored. This directory exists for documentation and deployment.
