# Scripts

Ops scripts for deploying and maintaining the PBX.

## Key Scripts

- `deploy.sh` — Push the Asterisk configs (dialplan, PJSIP, HTTP, ARI) to the
  Mac Mini PBX. See the header in the script for scope and usage.

Code rotation moved to the `tools/` CLI — see [`tools/`](../tools/). The operator
console will absorb rotation in Phase 2 (Eyes).
