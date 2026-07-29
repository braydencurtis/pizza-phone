# Asterisk Configuration

Dialplan, PJSIP endpoints, and the HTTP/ARI settings that let the Call Engine
drive live calls. Runs on the Mac Mini (Debian) PBX. See ADR-0001 for why call
control moved from per-call AGI scripts to a single long-running ARI/Stasis app.

## Key Files

- `extensions.conf` — dialplan. `[from-pots]` hands inbound Booth Phone calls to
  the engine via `Stasis(pizza-phone)`; `[pizza-success]` Dials the Upstairs
  Phone; `[pizza-fail]` says goodbye.
- `pjsip.conf` — SIP endpoints (Grandstream HT814 gateway, Yealink T46G at ext
  200).
- `http.conf` — enables Asterisk's built-in HTTP server on `:8088`, the
  transport ARI needs (REST + event WebSocket).
- `ari.conf` — enables ARI and defines the `pizza` user the engine authenticates
  as.

## How a call flows

1. The Booth Phone goes off-hook; the HT814 delivers the call into `[from-pots]`.
2. `Stasis(pizza-phone)` hands the channel to the Call Engine (the running
   `python -m engine`, registered as the `pizza-phone` app). Asterisk holds the
   channel in Stasis while the engine answers, plays prompts, and reads DTMF
   over ARI.
3. On success the engine sets `UPSTREAM_EXT` and `continue`s the channel into
   `[pizza-success]`, which `Dial()`s the Upstairs Phone. On failure/exile the
   engine hangs up. Either way the channel leaves Stasis.

`Stasis(pizza-phone)` and the engine's `ARI_APP` must be the same string; the
`ari.conf` user must match the engine's `ARI_USERNAME` / `ARI_PASSWORD`
(defaults: `pizza` / `pizza`, set in `engine/__main__.py`).

## Deploy

Copy the configs to Asterisk's config dir and reload each module. From this repo
on the PBX host:

```sh
sudo cp asterisk/http.conf asterisk/ari.conf asterisk/pjsip.conf asterisk/extensions.conf /etc/asterisk/
sudo asterisk -rx "module reload http"        # bring up the HTTP transport
sudo asterisk -rx "module reload res_ari"     # apply ari.conf
sudo asterisk -rx "pjsip reload"              # endpoints
sudo asterisk -rx "dialplan reload"           # from-pots -> Stasis cutover
```

Reload order matters: HTTP before ARI (ARI rides on it), and the engine must be
running before the dialplan starts routing to `Stasis(pizza-phone)` — otherwise
inbound channels reach Stasis with no app to receive them and fall through to
`Hangup()`.

## Verify

```sh
sudo asterisk -rx "http show status"          # server enabled, bound to :8088
sudo asterisk -rx "ari show status"           # ARI enabled
sudo asterisk -rx "ari show apps"             # lists "pizza-phone" once the engine connects
```

Then place a real call from the Booth Phone: the engine logs a `StasisStart`
and answers, and a successful code rings the Upstairs Phone via
`[pizza-success]`. This is the #15 DoD — a Stasis app receives the inbound
channel and the success path still Dials upstairs.

## Cutover

`[from-pots]` hands the call straight to `Stasis(pizza-phone)`. The AGI driver
that preceded it was retired in Phase 1 (issue #20) once the ARI path was proven
on the office rig, so there is no AGI fallback line to restore. If the engine is
down, inbound channels fall through Stasis to the trailing `Hangup()` — start
`python -m engine` and `dialplan reload` is not needed (the dialplan already
routes to Stasis).
