#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "systemd installer only supports Linux."
  exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <linux_user>"
  echo "Example: $0 ubuntu"
  exit 1
fi

LINUX_USER="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_SRC="${ROOT_DIR}/deploy/systemd/btc-crash-monitor.service"
SERVICE_TMP="$(mktemp)"

sed \
  -e "s#REPLACE_WITH_YOUR_USER#${LINUX_USER}#g" \
  -e "s#REPLACE_WITH_PROJECT_DIR#${ROOT_DIR}#g" \
  "${SERVICE_SRC}" > "${SERVICE_TMP}"

sudo cp "${SERVICE_TMP}" /etc/systemd/system/btc-crash-monitor.service
rm -f "${SERVICE_TMP}"

sudo systemctl daemon-reload
sudo systemctl enable --now btc-crash-monitor.service
sudo systemctl status --no-pager btc-crash-monitor.service
