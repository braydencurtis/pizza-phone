# Context

## Glossary

| Term | Definition |
|------|-----------|
| Booth Phone | The street-level landline phone connected via the Grandstream HT814 gateway. Callers pick this up. |
| Upstairs Phone | The Yealink T46G SIP phone upstairs that rings when a caller succeeds. Signals the operator to drop pizza. |
| PBX | The Asterisk PBX running on the Mac Mini (Debian) that routes calls between the two phones. |
| Code | The 4-digit PIN callers must dial to succeed. One active code per day. |
| Code Rotation | The act of generating a new Code. Currently done via a bash script (`rotate` command) that sends the code to Slack. |
| Current Backend | Asterisk dialplan configs (`.conf` files) + a bash script. No application backend exists yet. |
| Mode | The method by which callers obtain or earn the Code for a given day. Three modes exist: Tweeted, Audio Puzzle, Roguelike Phone-Tree. |
| Tweeted Mode | The Code is posted publicly (e.g., Twitter/X). Anyone who dials it succeeds. |
| Audio Puzzle Mode | Asterisk plays a `.wav` riddle. The answer is the Code. Caller dials the answer. |
| Roguelike Phone-Tree Mode | An infinitely looping DTMF maze. Caller picks up, navigates branching choices with no lives or attempt limit. Reaching the leaf node has the Code spoken aloud. Caller must then hang up and dial the Code to ring the Upstairs Phone. Tree is regenerated fresh per Call Session. |
| Prompt Library | A collection of pre-written spooky scenario prompts (audio or text-to-speech) used as nodes in the Roguelike tree. Authored by the team, not procedurally generated. |
| Puzzle Pool | A set of pre-recorded audio puzzles available for a given day. Callers receive one puzzle from the pool. |
| Attempt Limit | Maximum number of wrong answer attempts allowed per Call Session before disconnection. Set to 3. |
| Exile | The fail-state experience when a caller exhausts all attempts. A flavorful, thematic disconnect message. |
| Operator | The human upstairs who answers the Upstairs Phone and manually drops the pizza via dumbwaiter. |
| Call Session | One pickup-to-hangup interaction on the Booth Phone. May involve multiple DTMF attempts, audio prompts, and branching paths. |
| Call Engine | The single long-running backend process that owns live calls via ARI/Stasis, runs the game logic, and serves the Operator Console. Replaces the per-call AGI scripts. |
| Operator Console | The web dashboard the Operator uses to monitor calls, switch Modes, rotate Codes, and interact with a live caller (inject audio, trigger easter eggs, force hangup). |
| Talkback | An operator-initiated live two-way conversation: the Booth Phone caller is bridged to the Upstairs Phone so the Operator can speak with the guest, then returned into the game. Resumable, unlike Success. |
| Success Bridge | The reward bridge: caller reaches the Upstairs Phone, Operator drops pizza. Typically terminal. Distinct from Talkback, which is a mid-game detour. |
| Listen-in | The Operator hearing the live caller in the browser console, via an ARI Snoop tap on the call streamed over the WebSocket. Read-only — the caller cannot hear the Operator (unlike Talkback). |
| Call Recording | Every Call Session captured to disk as a mixed track plus separate inbound/outbound stems, keyed to the Call Session ID, for later editing and social media. |

## Decisions

| Decision | Status |
|----------|--------|
| Core infrastructure (phones, PBX, basic backend) | Done |
| Implementation order | Puzzles first, then backend built to support what proves necessary |
| Audio puzzle attempts | 3 attempts, then Exile message |
| Audio puzzle pool | Multiple puzzles available per day |
| Audio production | Pre-recorded human voice, not TTS |
| Roguelike tree | Infinite maze, no lives, pre-written prompts, tree regenerated per session |
| Roguelike code delivery | Code spoken at end of successful path; caller hangs up and dials it |
| Roguelike prompt delivery | TTS-generated audio, designed to be slightly uncanny for the backrooms aesthetic |
| Call logging / persistence | SQLite (stdlib, single file) is the store for Call Session history — core columns (session_id, timestamp, mode, outcome, duration, attempts) plus a JSON column for semi-structured per-mode detail (e.g. roguelike path). Chosen over JSONL because the console's needs are query-shaped (filter, paginate, join to recordings, hunt clips for video) and the switch is ~free while the persistence layer is being rewritten for the engine. Recordings stay as WAV files on disk; the DB holds their paths. |
| Backend language | Python (async / asyncio), driving calls via ARI/Stasis — not AGI |
| Anti-cheese strategy | Defer — trust the experience as deterrent. Physical gatekeeper if needed. |
| Config management | Web dashboard for operator to monitor calls, switch modes, rotate codes |
| Codebase structure | `core/` (channel-agnostic game logic, extracted from the old `agi/`), `engine/` (single asyncio process = ARI app + dashboard WS/HTTP server), `web/` (React + Vite frontend), `tools/` (operator/dev CLIs — code rotation, roguelike playground). `agi/` retired in Phase 1 once ARI proved out (#20); the code-rotation CLI moved to `tools/`. |
| Web dashboard auth | Single shared password (one line in config), gating both the HTTP load and the WebSocket upgrade. LAN-only, plain HTTP — mostly runs on the office network. Tailscale added later if remote access is ever needed; never a public port. |
| Web dashboard deployment | Systemd service, specific port |
| Real-time communication | WebSockets bidirectional between frontend and backend |
| Call audio architecture | Listen-in streamed to the browser (ARI Snoop → ExternalMedia → WebSocket); Talkback via handset bridge to the Upstairs Phone; recording via MixMonitor. See ADR-0002. |
| Recording set | Mixed track + inbound/outbound stems per call, keyed to Call Session ID |
| Talkback browser mute | Auto-mute the browser Listen-in during Talkback — default on, toggleable in the console |
| Consent notice | Deferred — decide later whether to bake a "call may be recorded" line into the intro |
| Call control | ARI/Stasis — a single long-running app owns live calls, enabling async operator interaction and live game-state (see ADR-0001). Replaces the per-call blocking AGI scripts and the earlier AMI-integration idea. |
| Operator interactions | Full v1 power set: speak-to-caller (live TTS), soundboard, listen-in, force success/exile, force hangup, reveal code, mode meddling (roguelike teleport, puzzle swap, live attempt-limit), dead air, Talkback (converse then return to game), rule-based easter eggs |
| Dashboard features | Current state view, mode toggle, code rotation, live call feed, past call logs |
| Console layout | Single-screen cockpit: the current call dominates the center (state, caller ID, duration, live DTMF, node/attempt, Listen-in + level), powers as a fixed control cluster around it, global mode/code in a top bar, past-calls + settings in a slide-over drawer that never hides the live call. Destructive actions (force-hangup/exile) use hold-to-confirm. Backrooms theming deferred to Phase 4. |
| Delivery phasing | Each phase leaves a working phone: (1) Parity + cutover — extract `core/`, ARI engine reproduces the three Modes, flip dialplan `AGI`→`Stasis`, retire `agi/`; (2) Eyes — read-only console: telemetry, past-calls (SQLite), Listen-in, mode/code control; (3) Hands — power set: force success/exile, force hangup, soundboard, speak-to-caller TTS, Talkback, recording; (4) Flair — easter eggs, theming. MVP line after Phase 3. |
