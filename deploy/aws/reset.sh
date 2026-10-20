#!/usr/bin/env bash
# Clean demo start in the cloud: the deployed equivalent of demo/reset.ps1.
#
#   bash deploy/aws/reset.sh
#
# DESTRUCTIVE: deletes Relay's Postgres, Kafka and Redis volumes and both
# store inboxes, and forgets the onboarded merchants. Then runs the smoke
# check against the deployment.

. "$(dirname "$0")/common.sh"
require_provisioned

echo "Wiping Relay and the stores..."
ssh_to "$RELAY_IP" 'cd ~/relay && docker compose down -v && docker compose up -d'
ssh_to "$STORES_IP" 'cd ~/relay && docker compose --profile sdk-demo down -v \
  && docker compose --profile sdk-demo up -d store-acme store-bluebird'
rm -f "$STATE_DIR/merchants.json"

wait_for_url "http://$RELAY_IP:8090/api/chart/endpoints" "Dashboard"
wait_for_url "http://$RELAY_IP:8000/docs" "Relay API"
wait_for_url "http://$STORES_IP:8071/healthz" "Acme Store"
wait_for_url "http://$STORES_IP:8072/healthz" "Bluebird Books"

PY="$REPO/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3
RELAY_HOST="$RELAY_IP" "$PY" "$REPO/demo/smoke_check.py"

print_urls
