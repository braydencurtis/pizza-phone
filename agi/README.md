# AGI Scripts

Python Asterisk Gateway Interface scripts that power the interactive call logic.

## Key Scripts

- `main.py` — Entry point; dispatches to the active mode handler
- `mode_tweeted.py` — Simple code verification (Tweeted mode)
- `mode_puzzle.py` — Plays audio puzzle, collects DTMF answer (Audio Puzzle mode)
- `mode_roguelike.py` — Generates and navigates the DTMF phone-tree (Roguelike mode)
- `code_manager.py` — Reads/writes the daily code and mode from config
- `logger.py` — Writes call session logs in JSON-lines format
