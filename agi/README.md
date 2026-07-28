# AGI Scripts

The AGI channel driver: the thin, Asterisk-specific layer that connects a live
call to the channel-agnostic game logic in [`core/`](../core/). Retired once the
ARI Call Engine proves out (see the epic, #13).

## Key Scripts

- `main.py` — Entry point invoked by the Asterisk dialplan. Builds an
  `AGICallIO` over the live channel and hands the interactive flow to
  `core.flow`.
- `agi_channel.py` — `AGIChannel`: raw AGI protocol over stdin/stdout.
- `agi_call_io.py` — `AGICallIO`: adapts `AGIChannel` to the `core.call_io.CallIO`
  protocol (play / read_dtmf / speak / hangup / to_success).
- `rotate.py` — CLI to rotate the daily code/mode and post to Slack.
- `slack_notifier.py` — Slack webhook notifier used by `rotate.py`.

All game logic — modes, router/dispatch, puzzle selection, roguelike tree,
TTS, config, and the call-session record — lives in `core/`.
