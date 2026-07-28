# Call audio: browser listen-in, handset talkback, and recording

Operator audio is split across two surfaces. The **browser console carries live caller audio** (Listen-in) so the room can hear calls as they happen; the **Upstairs Phone handset carries Talkback** (two-way voice). Every call is **recorded to disk** as a mixed track plus stems.

## Why

The operator will be at a laptop and wants to hear calls live in the room, so handset-only monitoring was rejected. Talkback voice stays on the handset because bridging an existing physical phone is trivial and avoids a browser microphone/WebRTC path. Recording is native to Asterisk and independent of both, so it comes cheaply.

## Mechanisms

- **Listen-in**: ARI Snoop channel (both directions) on the caller → ExternalMedia → RTP frames to the engine → forwarded over the dashboard WebSocket → played in the browser via the Web Audio API (`slin` PCM, no full WebRTC).
- **Talkback**: engine bridges the Upstairs Phone (by upstream extension, not device model) to the caller; operator speaks; "Return to game" pulls the caller back into Stasis.
- **Recording**: `MixMonitor` on the caller channel writes a mixed WAV **and** the two directional stems simultaneously, filenames keyed to the Call Session ID. Gitignored on disk, like logs. Optionally transcoded to mp3 later.
- Three taps (snoop, bridge, record) coexist on the one channel.

## Consequences

- The Snoop → ExternalMedia → RTP → WebSocket path is the most involved subsystem in the build — the accepted cost of in-room live audio.
- When Talkback is active the operator would otherwise hear the caller in both laptop and handset (with echo/delay), so the browser Listen-in **auto-mutes during Talkback by default**, but this is a console setting the operator can turn off (e.g. to let the room keep listening while one person talks).
- Recording members of the public for social media raises a consent question (intro notice or booth signage); deliberately deferred, not designed out.
