# PBX audio diagnosis — session handoff (2026-08-11)

Handoff for whoever picks up the "no audio on the win call" investigation. The
original fault is **fixed and verified**; what remains is audio *quality* tuning
plus several config-hygiene items.

Read this before touching the rig. Several non-obvious traps are documented at
the bottom and they cost real time to discover.

---

## 0. TL;DR

| | |
|---|---|
| **Original symptom** | Booth phone dials the code, upstairs rings, both answer, neither can hear the other |
| **Status** | **Fixed.** 90-second call verified with 0% packet loss both directions |
| **Root cause(s)** | `direct_media` was on (Asterisk handed media to the phones and left the path), and the Yealink was in a degraded state (silent ringer, dropped registration) |
| **Change made to the rig** | `direct_media=no` on 3 endpoints in `/etc/asterisk/pjsip.conf` |
| **Also done** | Yealink power-cycled by the user, which restored its registration and cleared a stale duplicate contact |
| **Still open** | Audio is quiet + upstairs hears loud hybrid echo; several config hygiene items |

---

## 1. Access

```sh
ssh foodbeast@100.122.188.77       # Tailscale address; hostname fb-deb, Debian 13
```

- Passwordless `sudo` works.
- **`asterisk` is not on `$PATH` for non-login shells** — always use the absolute
  form or `sudo asterisk -rx "..."` (sudo resolves it fine).
- Asterisk 22.10.1. Service has been up since 2026-08-03 (no restart in this session).

```sh
sudo asterisk -rx "pjsip show contacts"     # registration state — check this FIRST
sudo asterisk -rx "pjsip show endpoints"
sudo asterisk -rx "database get access code"   # today's 4-digit code
```

---

## 2. CRITICAL: the repo does not match the deployed rig

This is the single most important thing to know.

`/etc/asterisk/` runs a hand-written **"DEMO BUILD (2026-08-03)"**: pure dialplan,
**no ARI, no Stasis, no AGI**. The Python engine in `engine/` is *not running* and
is not involved in any live call.

| | Repo (`asterisk/`) | Deployed (`/etc/asterisk/`) |
|---|---|---|
| Endpoints | `ht814-gateway`, `yelink-200` | `entry`, `booth`, `upstairs` |
| Inbound | `Stasis(pizza-phone)` | pure dialplan, code compared inline |
| Code source | engine / `tools.rotate` | AstDB `access/code`, set by `/usr/local/bin/rotate-access-code.sh` (cron, daily) |

> **`scripts/deploy.sh` is a landmine.** Running it would overwrite the working
> config with endpoints that do not exist on the box — and would silently revert
> the `direct_media=no` fix. Do not run it until the drift is reconciled.

### Hardware / addressing

| Device | Address | Role |
|---|---|---|
| Grandstream HT814 | `10.0.5.11:5060` (FXS port 1 = `entry`) | Booth phone — an **old rotary phone**, pulse dial |
| Yealink T46G | `10.0.5.16:5060` (`upstairs`) | Operator phone. Config comments call it "retired" |
| HT814 FXS port 2 | `10.0.5.11:5062` | **Empty** — no handset. Was registered as a 2nd `upstairs` contact; gone after the Yealink power-cycle |
| PBX | `10.0.5.2` (`enp3s0f0`) | Asterisk. All three on the same L2 segment, no NAT |

Web UIs: HT814 `http://10.0.5.11` (port 80 open), Yealink `http://10.0.5.16`.
The HT814 does not answer ICMP — that is normal, not a fault.

### Dialplan shape (`/etc/asterisk/extensions.conf`)

- `[from-pots]` — the *dialled number IS the code*, tested before answering. A
  non-matching 4-digit dial gets `vm-incorrect` + `vm-goodbye`. A touch-tone path
  with `Read()` exists for other phones.
  > **This is a choice of the demo build, not a hardware limit.** The HT814
  > decodes the booth phone's pulse dialing and sends digits as DTMF, including
  > mid-call — so in-call digit entry works on the rotary handset, and the Audio
  > Puzzle and Roguelike Modes are viable on it as specced.
- Win path: `Answer()` → `Playback(auth-thankyou)` → `Dial(PJSIP/upstairs,30)`.
- `[from-internal]` (upstairs only): `1000`/`100` rings the booth, `6000`/`600`
  Echo test, `700` self-test that sends the code as DTMF.
- **`[from-pots]` has no test extension** — dialling `6000` from the booth just
  gets the wrong-code prompts.

---

## 3. What was wrong

### Fault A — `direct_media` (fixed)

`direct_media` defaults to `yes` in PJSIP and the deployed config never overrode
it. On answer, Asterisk re-INVITEd both phones to stream to each other and
dropped out of the media path.

Proof, from `rtp set debug` on the pre-fix call at 12:43:58:

```
12:43:58 – 12:44:21   booth → Asterisk   50 packets/sec, steady
12:44:22              upstairs answers
12:44:22 – 12:45:09   booth → Asterisk   ZERO packets for 46 seconds
```

The booth stopped sending to Asterisk at the instant of answer — the signature of
the re-INVITE. Post-fix, the booth streamed for the full call with no flatline.

**Fix applied** — `direct_media=no` appended after each of the three
`dtmf_mode=` lines (one per endpoint: `entry`, `booth`, `upstairs`):

```sh
sudo sed -i "/^dtmf_mode=/a direct_media=no" /etc/asterisk/pjsip.conf
sudo asterisk -rx "pjsip reload"      # see the reload warning in §5 first
```

Backup of the original: `/etc/asterisk/pjsip.conf.bak-20260811-124919`

**Do not turn `direct_media` back on**, even though fault B may have been
sufficient to explain the outage on its own. ADR-0002 requires Asterisk to hold
the media: Listen-in (ARI Snoop), Talkback, and `MixMonitor` recording all break
if the Success Bridge streams phone-to-phone. Relay cost at one concurrent call
is negligible.

### Fault B — the Yealink was degraded (fixed by power-cycle)

Confirmed by the user: **every call before the hard restart rang silently** (call
visible on screen, no audible ring); **every call after rang normally.** It had
also dropped its SIP registration entirely and was not re-registering.

A power-cycle restored it and, as a bonus, the stale `10.0.5.11:5062` contact did
not come back — so `upstairs` now has exactly one contact and no longer forks.

Attribution between A and B is not cleanly separable. B was present for every
failed test, so it may have been sufficient alone; A is proven to have moved the
media off Asterisk regardless. Both are now resolved.

### Verification

90-second call, `7249`, two people:

| Stream | Packets | Lost | Reordered |
|---|---|---|---|
| Booth → Asterisk | 4,536 | 0 | 0 |
| Yealink → Asterisk | 4,193 | 0 | 0 |

Steady 50/sec start to finish. **The network is not implicated in anything.**

---

## 4. Open items

### 4a. Audio quality — the active task

User reports, on the working call:

1. Volume "a little quiet" in both directions.
2. **The upstairs party hears their own voice echoed, louder than the booth
   caller's voice.**
3. Crackle for the first half of the call that cleared up as they stayed on.

Diagnosis (not yet measured): **hybrid / line echo at the HT814's FXS port.** The
2-wire↔4-wire conversion reflects part of the incoming signal back when the
hybrid's balance impedance doesn't match the connected phone. An old rotary phone
is close to a worst case — built for a 600 Ω resistive loop with an anti-sidetone
coil balanced for that era.

Symptoms 1 and 2 are likely one gain-structure problem: RX gain too high (strong
signal into the hybrid → strong reflection) and TX gain too low (booth voice
arrives weak). Excessive RX gain also pushes the reflection into clipping, and
echo cancellers can only cancel a *linear* echo path — which would also explain
symptom 3 as the canceller struggling to converge.

**All fixes are on the HT814 at `http://10.0.5.11`, FXS Port 1 settings. Nothing
in Asterisk** — it performs no echo cancellation on a PJSIP↔PJSIP call.

Suggested order, one change at a time:
1. Impedance / SLIC setting → 600 Ω (fixes the reflection at source)
2. TX gain +3 to +6 dB (fixes the quiet far end)
3. RX gain −3 to −6 dB (reduces echo, keeps the canceller linear)
4. Confirm echo cancellation is enabled on the port

**Offered but not yet run — the measurement protocol.** `sox` is installed and
`mixmonitor start <channel> <file>` works from the CLI, so the levels can be
measured rather than guessed:

- Upstairs talks 15s, booth silent → whatever lands in the booth's inbound stream
  *is* the echo; measure its level.
- Booth talks 15s, upstairs silent → that's the wanted-signal level.
- Compare with `sox <file> -n stat` → echo return loss in dB, and exactly how many
  dB each gain should move.

Recordings land in `/var/spool/asterisk/monitor/`. Clean them up afterwards.

### 4b. Config hygiene

- **`max_contacts=2` → `1`** on the `upstairs` AOR. The duplicate registration is
  gone, but the setting that let an empty FXS port compete for win calls remains.
  Its own comment calls it a workaround, not a fix.
- **`qualify_frequency` is 0** (contacts show `NonQual`) — Asterisk never checks
  whether the upstairs phone is reachable. Given that this phone just proved it
  can fail silently, and the entire game depends on the Operator hearing it ring,
  this is the highest-value reliability fix. Without it a dead upstairs phone is
  discovered by a caller who solved the puzzle and got nothing.
- **Upstairs identity is unresolved** — config says the Yealink is "retired" and
  the intended upstairs phone is HT814 FXS port 2, which is empty. The game
  currently depends on a device the config says shouldn't be there.
- **Repo/rig drift** (§2) and the fact that `direct_media=no` exists *only* on the
  box. Fold it into whatever config becomes authoritative.
- Optional, previously discussed: a booth-side test extension in `[from-pots]`.
  If added, note that codes are uniform random `0000`–`9999`, so any reserved
  4-digit extension has a 1-in-10,000 chance per rotation of shadowing the win
  path. Pair it with a re-roll guard in `rotate-access-code.sh`.

---

## 5. Traps — read before instrumenting

Each of these cost time or caused a problem in this session.

- **`pjsip reload` is dangerous on this rig.** The deployed `pjsip.conf` header
  warns that reloading `res_pjsip.so` "silently drops the transport object while
  leaving the socket bound — that is what broke this rig in July." A `pjsip reload`
  was run in this session and both `upstairs` contacts dropped registration within
  ~15 minutes; recovery required power-cycling the phone. If config must change,
  prefer a scheduled `sudo systemctl restart asterisk` and expect to re-register
  the devices.

- **`Echo()` is a false-negative test on the HT814.** Its line echo canceller
  suppresses exactly the signal `Echo()` returns, so a perfectly healthy booth
  phone tested as silent. This produced a wrong "dead microphone" conclusion.
  Use `Playback`/`Milliwatt` for the Asterisk→phone direction and packet counts
  for the phone→Asterisk direction instead.

- **`pjsip show channelstats` stopped listing the channel once answered** when
  polled in a loop. Unreliable here — use `rtp set debug on` plus a logger channel.

- **`rtp set debug` does not log the transmit side while bridging.** Only "Got"
  lines are complete. Count received streams; do not read anything into the
  near-zero "Sent" counts.

- **`asterisk -rvvv` redirected to a file captures nothing** (no TTY — it emits a
  banner and megabytes of padding spaces). Capture with:
  ```sh
  sudo asterisk -rx "logger add channel mycap.log notice,warning,error,verbose"
  sudo asterisk -rx "core set verbose 3"    # needed for Dial/answer messages
  sudo asterisk -rx "pjsip set logger on"   # SIP with SDP
  sudo asterisk -rx "rtp set debug on"      # per-packet
  # ... then reverse all four, and `logger remove channel mycap.log`
  ```

- **Never `rm` a log file Asterisk has open** — it keeps writing to the unlinked
  inode and the new file stays empty. Use a fresh filename per capture.

- **`channel originate` only rings for ~30s**, which is not enough time to walk to
  a street-level phone. Ring in a retry loop and poll
  `core show channels concise` for `!Up!`.

- **Dial tone proves nothing about the network.** The HT814 generates it locally;
  it never crosses SIP or RTP. It does prove the tip/ring pair and handset
  receiver are good.

---

## 6. State left behind

- All diagnostic scripts and capture logs removed from the box (`/tmp`,
  `/var/log/asterisk`).
- No logger channels active; `rtp set debug` off; `pjsip set logger` off;
  verbose back to 0.
- Only artifact retained: `/etc/asterisk/pjsip.conf.bak-20260811-124919`.
- Live config confirmed: `direct_media : false` on `entry`, `booth`, `upstairs`.
- Nothing committed to the repo by this session other than this document.
