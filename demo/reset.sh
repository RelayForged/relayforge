#!/usr/bin/env sh
# Clean demo start: wipe all stack data, rebuild, wait for health, smoke check.
#
#   sh demo/reset.sh
#
# DESTRUCTIVE: `down -v` deletes the Postgres, Kafka and Redis volumes.
# Every event, delivery and attempt recorded so far is gone.
set -eu
cd "$(dirname "$0")/.."

echo "Stopping the stack and deleting its volumes..."
docker compose down -v

echo "Building and starting..."
docker compose up -d --build

services="kafka postgres redis api dashboard simulator"
deadline=$(( $(date +%s) + 240 ))
while :; do
    status=$(docker compose ps --format '{{.Service}} {{.Status}}')
    ready=0
    for s in $services; do
        echo "$status" | grep -q "^$s .*(healthy)" && ready=$((ready + 1))
    done
    [ "$ready" -eq 6 ] && break
    if [ "$(date +%s)" -gt "$deadline" ]; then
        echo "$status"
        echo "Services not healthy after 4 minutes" >&2
        exit 1
    fi
    sleep 3
done
echo "All services healthy."

python=".venv/bin/python"
[ -x "$python" ] || python=".venv/Scripts/python.exe"
[ -x "$python" ] || python="python3"
"$python" demo/smoke_check.py

echo
echo "Ready: http://localhost:8090"
