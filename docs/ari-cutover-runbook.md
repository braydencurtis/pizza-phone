# ARI cutover — first live-call runbook

The one-time checklist for proving the ARI Call Engine on the office rig (issue
#20 DoD: "phone works on ARI for all three Modes"). Everything up to this point
is green in tests, but the **real** ARI seam — Asterisk ↔ the live `ARIClient`,
real audio, real DTMF, real upstairs ring — has never run on hardware. This is
that run.

Do it at the rig with the Booth Phone and the Upstairs Phone (Yealink, ext 200)
in reach. Budget ~30 min.

---

## 0. Preconditions

On the Mac Mini (PBX host):

- [ ] Repo checked out and up to date (`git pull` on `main`).
- [ ] Python deps installed in a venv: `python -m venv .venv && .venv/bin/pip install -e .`
- [ ] A TTS backend for roguelike: `espeak` installed (`which espeak`), or
      `scripts/deploy.sh <ip> --install-tts`.
- [ ] Puzzle audio present: at least one `.wav` under `audio/puzzles/`.
- [ ] **Asterisk can READ the audio + TTS files.** Asterisk runs as the
      `asterisk` user; the engine writes TTS to `/tmp` and reads puzzle WAVs from
      the repo checkout. If those aren't world-readable (or `/tmp` is private),
      playback silently fails — see Step 4's audio check. `ls -l audio/puzzles/`
      and confirm `asterisk` can traverse the path.

---

## 1. Deploy the Asterisk configs

From your laptop (or on the host, adapt paths):

```sh
scripts/deploy.sh <mac-mini-ip> --restart
```

This pushes `extensions.conf` / `pjsip.conf` / `http.conf` / `ari.conf` to
`/etc/asterisk/` and reloads in the required order (http → res_ari → pjsip →
dialplan). To do it by hand, see `asterisk/README.md` § Deploy — **order
matters**: HTTP before ARI.

---

## 2. Start the engine (watch it live)

For the test session, run it in the foreground so you can watch events — don't
background it under systemd yet (that's a later hardening step). In a tmux pane
on the host:

```sh
cd <repo>
.venv/bin/python -m engine
```

Defaults (override via env if needed): `ARI_BASE_URL=http://localhost:8088`,
`ARI_USERNAME=pizza`, `ARI_PASSWORD=pizza`, `ARI_APP=pizza-phone`. You should see
`ARI client connected … as app 'pizza-phone'` and `Call engine running`.

The SQLite history file is created automatically at `data/calls.db`.

---

## 3. Verify the wiring (before touching the phone)

```sh
sudo asterisk -rx "http show status"   # server enabled, bound to :8088
sudo asterisk -rx "ari show status"    # ARI enabled
sudo asterisk -rx "ari show apps"      # must list: pizza-phone
```

If `ari show apps` does **not** list `pizza-phone`, the engine isn't connected —
stop here and fix (wrong `ARI_APP`, bad ARI creds, or HTTP/ARI not reloaded).
The dialplan already routes `[from-pots]` to `Stasis(pizza-phone)`, so with no
app registered, inbound calls fall straight to `Hangup()`.

---

## 4. Per-Mode call tests

Set each Mode with the rotation CLI (from the repo root on the host), then call
the Booth Phone. Watch the engine's tmux pane for `StasisStart` and the
`Session … ended: mode=… outcome=…` line after each call.

### 4a. Tweeted — code entry, wrong-then-right, exile

```sh
.venv/bin/python -m tools.rotate --mode tweeted --code 1234
```

- [ ] **Success:** call, dial `1234` → the Upstairs Phone (ext 200) rings.
      *(Validates: answer, DTMF collect, `continue` into `[pizza-success]`,
      Dial upstairs.)*
- [ ] **Wrong-then-right:** call, dial `0000` → you hear the wrong-answer beep;
      then dial `1234` → upstairs rings.
- [ ] **Exile:** call, dial three wrong 4-digit codes → after the 3rd you hear
      the Exile prompt (`voicemail/busy`) and the call hangs up. No upstairs ring.

### 4b. Puzzle — **this validates absolute-path audio + file permissions**

```sh
.venv/bin/python -m tools.rotate --mode puzzle --code 4242   # code = the riddle's answer
```

- [ ] **You actually hear the riddle WAV play.** This is the single most likely
      thing to fail: the engine names the file to Asterisk as an absolute
      `sound:<repo>/audio/puzzles/riddle-xxx` URI. If you hear **silence** where
      the riddle should be, it's almost certainly the absolute `sound:` path or
      Asterisk file permissions (Step 0). Check the engine pane and
      `sudo asterisk -rx "core show channels"` / CLI for playback errors.
- [ ] Dial the answer → upstairs rings. Wrong-then-right and exile behave as in 4a.

### 4c. Roguelike — **this validates live TTS (/tmp) playback**

```sh
.venv/bin/python -m tools.rotate --mode roguelike --code 7777
```

- [ ] **You hear spoken (TTS) room prompts**, uncanny and a bit robotic. Silence
      here means espeak isn't installed or Asterisk can't read the `/tmp` TTS
      output (Step 0 permissions again).
- [ ] Navigate with `1`/`2`; at a leaf you hear "The code is 7777. Hang up and
      dial it now."
- [ ] Hang up, call back, dial `7777` → upstairs rings. *(The tree regenerates
      per call — the path won't be the same twice; that's expected.)*

---

## 5. Deliberate edge checks

- [ ] **Hang up mid-prompt.** During the puzzle riddle or a roguelike prompt,
      hang up the Booth Phone. Confirm the engine logs the session ending and
      returns to idle (`active_session` cleared) — i.e. the call slot frees. If
      the pane goes quiet and a *new* call is answered but never runs, the engine
      is stuck waiting on a playback that never finished (see Review finding #2).
- [ ] **Second concurrent call.** With one call live, pick up… well, there's one
      Booth Phone, so simulate if you can (a second SIP channel into `from-pots`).
      Expected: the second channel is hung up immediately, the first is unaffected.
- [ ] **Check persistence.** After the calls:
      `sqlite3 data/calls.db "select mode, outcome, attempts from calls order by started_at desc limit 10;"`
      — every call above should have a row with the right mode/outcome.

---

## 6. If something fails

- **No `pizza-phone` in `ari show apps`** → engine not connected. Check the
  engine pane for auth/connection errors; confirm `ari.conf` `[pizza]` password
  matches `ARI_PASSWORD`; confirm `module reload http` ran before `res_ari`.
- **Call answered but silent prompts** → audio file resolution / permissions
  (Step 0 + 4b/4c). This is the top suspect. Try a known-good builtin first:
  temporarily point a mode at a builtin sound to isolate (e.g. confirm the
  wrong-answer `beep` and Exile `voicemail/busy` builtins *do* play — they're
  relative `sound:` names and should always work; if builtins play but your WAVs
  don't, it's the absolute-path/permission issue).
- **Upstairs never rings on success** → check `[pizza-success]` Dial and that
  `PJSIP/200` (the Yealink) is registered: `sudo asterisk -rx "pjsip show endpoints"`.
- **WS drops / Asterisk restarted mid-session** → the engine does **not**
  auto-reconnect (Review finding #3). Restart `python -m engine`.

---

## Rollback

There is no AGI fallback — `agi/` was retired in #20 and the dialplan has routed
to Stasis since #15. "Rollback" means: stop the engine, and inbound calls fall
through Stasis to `Hangup()` (dead phone, but nothing else breaks). Fix forward.
