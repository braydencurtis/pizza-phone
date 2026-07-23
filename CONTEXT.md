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
| Call logging | JSON lines file — one line per Call Session with timestamp, mode, outcome, duration, attempts, and path |
| Backend language | Python via AGI |
| Anti-cheese strategy | Defer — trust the experience as deterrent. Physical gatekeeper if needed. |
| Config management | Config files for now — no web dashboard |
