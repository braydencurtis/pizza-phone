# Backrooms Phone Booth — Project Brief

## Concept

A spooky liminal space / backrooms-themed food event. An out-of-place phone or phone booth sits on the street. To get free pizza (lowered via dumbwaiter), people must call a 4-digit code that rings an upstairs phone. The code is gamified — not just tweeted out, but earned through puzzles.

## Hardware

| Item | Role |
|------|------|
| Grandstream HT814 (analog-to-SIP gateway) | Connected to the booth phone (landline/phone booth), sits on the Mac Mini's network |
| Yealink T46G (SIP phone) | "Upstairs phone" — answers the call once someone dials the correct code |
| Mac Mini running Debian | Runs Asterisk PBX, both phones are already registered and can call each other |

Both phones are already registered to Asterisk. Calling between them using extension numbers works.

## What We Need to Build

### Core Dialplan Logic

The booth phone needs more than a direct dial — it needs a game experience before the upstairs phone rings.

### Code Delivery Modes (configurable per day)

1. **Tweeted code** — The 4-digit code is posted publicly; anyone who dials it gets pizza.
2. **Audio puzzle** — When the booth is picked up, Asterisk plays a `.wav` file containing a riddle or puzzle. The answer IS the 4-digit code. Caller dials the answer, upstairs phone rings.
3. **Roguelike phone-tree** — A randomly generated DTMF phone-tree (press 1, press 2, etc.) that the caller navigates. Wrong choices loop back, give hints, or dead-end. The code is waiting at the end of a successful run.

### Pizza Drop

When someone solves the puzzle and the upstairs phone rings, someone upstairs answers and manually lowers the pizza. For now, no automation needed — just the call ringing the Yealink is the signal.

### Backend Requirements

- Manage the daily 4-digit code (set, change, view)
- Select which "mode" is active that day (tweeted / audio puzzle / roguelike tree)
- Call logging — who called, what path they took, whether they succeeded
- Anti-cheese considerations:
  - Same code for all callers on a given day (not unique per call)
  - Possible call limits or rate limiting (TBD)
- Potential web dashboard (TBD — could be a simple config file or CLI to start)

## Open Questions

| Question | Notes |
|----------|-------|
| Where does development happen? | Likely write code locally, SSH/sync to the Mac Mini running Debian |
| Backend language? | Node.js, Python, Go — TBD |
| Roguelike phone-tree depth? | Thinking 3-5 levels of DTMF choices, randomly generated each session |
| Roguelike randomness? | Randomized per call, so each caller gets a unique path |
| Audio puzzles? | Need a pipeline for recording/importing `.wav` files and mapping them to answers |
| Web dashboard? | Nice to have — code management, call logs, status. Could start with config file. |
| Anti-cheese? | Rate limiting? Call limits? Max attempts per caller? |
| Multiple pizzas? | What happens when 5 people solve it — does the Yealink keep ringing? |

## Asterisk Config to Review

Once the repo is set up, we'll need to review the existing Asterisk config:
- `extensions.conf` — current dialplan
- `sip.conf` or `pjsip.conf` — phone registrations
- Any existing AGI or app_* integrations

## Tech Stack (TBD)

- Asterisk (already running)
- Backend: [language TBD]
- Frontend (optional): [framework TBD]
- Audio: `.wav` files for puzzles
- Storage: code state, call logs, mode config
