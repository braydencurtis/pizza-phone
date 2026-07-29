#!/usr/bin/env bash
set -euo pipefail

# Deploy the pizza-phone Asterisk configs to the Debian Mac Mini PBX.
#
# Usage:
#   scripts/deploy.sh <mac-mini-ip> [--restart] [--install-tts]
#
# Requires SSH access for foodbeast@<mac-mini-ip> with sudo privileges.
#
# Scope: this pushes the Asterisk dialplan/PJSIP/HTTP/ARI configs to
# /etc/asterisk. The Call Engine itself (python -m engine) and the game logic in
# core/ run from a git checkout on the host under a systemd service — deploying
# and restarting that is out of this script's scope. The old AGI driver (agi/)
# was retired in Phase 1 (#20); nothing is installed into /var/lib/asterisk/agi
# anymore.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <mac-mini-ip> [--restart] [--install-tts]"
  exit 1
fi

TARGET="$1"
shift

RESTART=false
INSTALL_TTS=false

for arg in "$@"; do
  case "$arg" in
    --restart) RESTART=true ;;
    --install-tts) INSTALL_TTS=true ;;
  esac
done

REMOTE_USER="${REMOTE_USER:-foodbeast}"
ASTERISK_CONF_DIR="/etc/asterisk"

echo "==> Deploying Asterisk configs to ${REMOTE_USER}@${TARGET}"

# SSH helper with TTY allocation so sudo can prompt for password
ssh_cmd() {
  ssh -t "${REMOTE_USER}@${TARGET}" "$@"
}

# Resolve absolute remote home and staging directory
REMOTE_HOME="$(ssh "${REMOTE_USER}@${TARGET}" 'echo ~')"
REMOTE_STAGING="${REMOTE_HOME}/pizza-phone-staging"

# Ensure staging directory exists
ssh "${REMOTE_USER}@${TARGET}" "mkdir -p ${REMOTE_STAGING}/asterisk"

# Deploy Asterisk configs to a user-writable staging dir (no sudo needed).
# All four are needed for the ARI cutover: http.conf brings up the transport,
# ari.conf enables ARI + the engine's user, extensions.conf routes to Stasis,
# pjsip.conf defines the endpoints.
echo "==> Syncing asterisk configs -> remote staging"
rsync -avz \
  "${ROOT_DIR}/asterisk/extensions.conf" \
  "${ROOT_DIR}/asterisk/pjsip.conf" \
  "${ROOT_DIR}/asterisk/http.conf" \
  "${ROOT_DIR}/asterisk/ari.conf" \
  "${REMOTE_USER}@${TARGET}:${REMOTE_STAGING}/asterisk/"

# Move staged files into /etc/asterisk with a single sudo call
echo "==> Installing configs (sudo required)..."
ssh_cmd "
  set -e
  sudo mkdir -p ${ASTERISK_CONF_DIR}
  sudo cp ${REMOTE_STAGING}/asterisk/extensions.conf ${ASTERISK_CONF_DIR}/
  sudo cp ${REMOTE_STAGING}/asterisk/pjsip.conf ${ASTERISK_CONF_DIR}/
  sudo cp ${REMOTE_STAGING}/asterisk/http.conf ${ASTERISK_CONF_DIR}/
  sudo cp ${REMOTE_STAGING}/asterisk/ari.conf ${ASTERISK_CONF_DIR}/
  echo 'Configs installed successfully'
"

# Install TTS backend if requested
if [[ "$INSTALL_TTS" == true ]]; then
  echo "==> Installing espeak on remote host..."
  ssh_cmd "sudo apt-get update && sudo apt-get install -y espeak"
fi

# Reload Asterisk if requested. HTTP before ARI (ARI rides on it); the engine
# must be running before the dialplan starts routing to Stasis(pizza-phone).
if [[ "$RESTART" == true ]]; then
  echo "==> Reloading Asterisk..."
  ssh_cmd "
    sudo asterisk -rx 'module reload http'
    sudo asterisk -rx 'module reload res_ari'
    sudo asterisk -rx 'pjsip reload'
    sudo asterisk -rx 'dialplan reload'
  "
  echo "==> Asterisk reloaded"
fi

echo "==> Deployment complete"
