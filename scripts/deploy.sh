#!/usr/bin/env bash
set -euo pipefail

# Deploy pizza-phone AGI scripts and Asterisk configs to a Debian Mac Mini.
#
# Usage:
#   scripts/deploy.sh <mac-mini-ip> [--restart] [--install-tts]
#
# Requires SSH access for foodbeast@<mac-mini-ip> with sudo privileges.

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
AGI_REMOTE_DIR="/var/lib/asterisk/agi"
ASTERISK_CONF_DIR="/etc/asterisk"

echo "==> Deploying to ${REMOTE_USER}@${TARGET}"

# SSH helper with TTY allocation so sudo can prompt for password
ssh_cmd() {
  ssh -t "${REMOTE_USER}@${TARGET}" "$@"
}

# Resolve absolute remote home and staging directory
REMOTE_HOME="$(ssh "${REMOTE_USER}@${TARGET}" 'echo ~')"
REMOTE_STAGING="${REMOTE_HOME}/pizza-phone-staging"

# Ensure staging directory exists
ssh "${REMOTE_USER}@${TARGET}" "mkdir -p ${REMOTE_STAGING}"

# Deploy AGI scripts to user-writable staging dir (no sudo needed)
echo "==> Syncing agi/ -> remote staging"
rsync -avz --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${ROOT_DIR}/agi/" \
  "${REMOTE_USER}@${TARGET}:${REMOTE_STAGING}/agi/"

# Deploy channel-agnostic game logic (imported by agi/main.py)
echo "==> Syncing core/ -> remote staging"
rsync -avz --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${ROOT_DIR}/core/" \
  "${REMOTE_USER}@${TARGET}:${REMOTE_STAGING}/core/"

# Deploy Asterisk dialplan configs
echo "==> Syncing asterisk configs -> remote staging"
rsync -avz \
  "${ROOT_DIR}/asterisk/extensions.conf" \
  "${ROOT_DIR}/asterisk/pjsip.conf" \
  "${REMOTE_USER}@${TARGET}:${REMOTE_STAGING}/asterisk/"

# Deploy audio files
if [[ -d "${ROOT_DIR}/audio" ]]; then
  echo "==> Syncing audio/ -> remote staging"
  rsync -avz \
    "${ROOT_DIR}/audio/" \
    "${REMOTE_USER}@${TARGET}:${REMOTE_STAGING}/audio/"
fi

# Now move staged files to system directories with a single sudo call
echo "==> Installing files (sudo required)..."
ssh_cmd "
  set -e
  sudo mkdir -p ${AGI_REMOTE_DIR} ${ASTERISK_CONF_DIR}
  sudo rm -rf ${AGI_REMOTE_DIR}/agi && sudo cp -r ${REMOTE_STAGING}/agi ${AGI_REMOTE_DIR}/
  sudo rm -rf ${AGI_REMOTE_DIR}/core && sudo cp -r ${REMOTE_STAGING}/core ${AGI_REMOTE_DIR}/
  sudo cp ${REMOTE_STAGING}/asterisk/extensions.conf ${ASTERISK_CONF_DIR}/
  sudo cp ${REMOTE_STAGING}/asterisk/pjsip.conf ${ASTERISK_CONF_DIR}/
  if [ -d '${REMOTE_STAGING}/audio' ]; then
    sudo cp -r ${REMOTE_STAGING}/audio ${AGI_REMOTE_DIR}/
  fi
  echo 'Files installed successfully'
"

# Install TTS backend if requested
if [[ "$INSTALL_TTS" == true ]]; then
  echo "==> Installing espeak on remote host..."
  ssh_cmd "sudo apt-get update && sudo apt-get install -y espeak"
fi

# Restart Asterisk if requested
if [[ "$RESTART" == true ]]; then
  echo "==> Reloading Asterisk..."
  ssh_cmd "sudo asterisk -rx 'module reload'"
  echo "==> Asterisk reloaded"
fi

echo "==> Deployment complete"
