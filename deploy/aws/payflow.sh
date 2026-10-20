#!/usr/bin/env bash
# PayFlow from this machine, against the deployed Relay. See payflow.ps1.
#
#   bash deploy/aws/payflow.sh pay acme 499

. "$(dirname "$0")/common.sh"
require_provisioned

PY="$REPO/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

cd "$REPO"
# UTF-8 output: amounts are printed with the rupee sign, which the Windows
# console's default code page cannot encode.
RELAY_URL="http://$RELAY_IP:8000" PYTHONIOENCODING=utf-8 PAYFLOW_STATE="$(native_path "$STATE_DIR/merchants.json")" \
  exec "$PY" -m examples.payflow "$@"
