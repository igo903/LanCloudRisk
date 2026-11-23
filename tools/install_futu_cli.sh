#!/usr/bin/env bash
# Run the official, signature-verifying installer with HTTP/1.1 for every curl request.
set -euo pipefail

risk_futu_install_tmp="$(mktemp -d "${TMPDIR:-/tmp}/futu-cli-install.XXXXXX")"
trap 'rm -rf "$risk_futu_install_tmp"' EXIT

curl() {
  command curl --http1.1 --retry 3 --retry-delay 2 \
    --connect-timeout 20 --max-time 180 "$@"
}
export -f curl

curl -fsSL https://raw.githubusercontent.com/FutunnOpen/futu-cli/main/scripts/install.sh \
  -o "$risk_futu_install_tmp/install.sh"
FUTU_CLI_NO_MODIFY_PATH=1 "$BASH" "$risk_futu_install_tmp/install.sh"
"${INSTALL_DIR:-$HOME/.futu/bin}/futu" version
