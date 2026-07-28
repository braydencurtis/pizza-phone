# Call control via ARI/Stasis, not AGI or AMI

The dashboard must let the operator interact with a live call at any moment — inject audio, trigger easter eggs, force a hangup — while also showing the caller's real-time game state (mode, tree node, attempt count). We are moving call control from the current per-call blocking AGI scripts to a single long-running **ARI (Asterisk REST Interface) / Stasis** application.

## Why

AGI (and FastAGI) is synchronous: while the script blocks inside `stream_file` or `read_digits`, the channel is deaf to the outside world, so mid-call operator audio injection is only achievable through hacks. AMI is a control/monitoring side-channel that does not own the media flow. ARI is event-driven — Asterisk pushes channel events (DTMF, hangup, playback-finished) over a WebSocket and the app issues commands over REST — so the operator can act on a channel at any instant, and the long-running app naturally holds all live game state for the dashboard to read.

## Considered options

- **AGI + AMI sidecar** — least rewrite, but live audio injection stays fragile.
- **FastAGI** — long-running server holds state, but still a synchronous control model; async injection requires chopping audio into interruptible chunks.
- **ARI / Stasis** (chosen) — fully asynchronous; the union of "owns the media flow" and "reachable by the operator at any time."

## Consequences

- The game *logic* (code checking, puzzle selection, roguelike tree, TTS, logging) is portable; the channel driver (`AGIChannel`) is replaced by an async ARI media/DTMF layer.
- Requires enabling Asterisk's `http.conf` and `ari.conf` (an ARI user/password).
- The dashboard backend *is* the call engine now, not a bystander. One long-running asyncio process serves ARI, the dashboard WebSocket, and config/HTTP. If it crashes, calls break — whereas today an AGI crash kills only one call. This coupling is the accepted cost of live operator power.
