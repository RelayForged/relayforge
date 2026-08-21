#!/usr/bin/env bash
# Week 2 smoke: POST /events -> GET /events/{id}, then confirm Postgres + Kafka.
#
# Usage (from repo root, with docker compose up):
#   ./scripts/api_smoke.sh
#
# If nothing is listening on BASE_URL, this script starts uvicorn and stops it
# on exit. Set BASE_URL to point at an already-running API instead.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
UVICORN_PID=""

cleanup() {
  if [[ -n "$UVICORN_PID" ]] && kill -0 "$UVICORN_PID" 2>/dev/null; then
    kill "$UVICORN_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

api_up() {
  curl -sf -o /dev/null "${BASE_URL}/docs" || curl -sf -o /dev/null "${BASE_URL}/openapi.json"
}

if ! api_up; then
  echo "API not reachable at ${BASE_URL}; starting uvicorn..."
  "${ROOT}/.venv/bin/uvicorn" relay.api.main:app --host 127.0.0.1 --port 8000 >/tmp/relay-api-smoke.log 2>&1 &
  UVICORN_PID=$!
  for _ in $(seq 1 30); do
    if api_up; then
      break
    fi
    sleep 0.2
  done
  if ! api_up; then
    echo "uvicorn failed to start. Log:"
    cat /tmp/relay-api-smoke.log || true
    exit 1
  fi
fi

POST_BODY=$(curl -sS -w "\n%{http_code}" -X POST "${BASE_URL}/events" \
  -H "Content-Type: application/json" \
  -d '{"event_type":"OrderCreated","payload":{"order_id":"week2-smoke","amount":42}}')
POST_CODE=$(echo "$POST_BODY" | tail -n 1)
POST_JSON=$(echo "$POST_BODY" | sed '$d')

if [[ "$POST_CODE" != "202" ]]; then
  echo "POST /events expected 202, got ${POST_CODE}: ${POST_JSON}"
  exit 1
fi

EVENT_ID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['event_id'])" "$POST_JSON")
echo "POST /events -> 202 event_id=${EVENT_ID}"

GET_BODY=$(curl -sS -w "\n%{http_code}" "${BASE_URL}/events/${EVENT_ID}")
GET_CODE=$(echo "$GET_BODY" | tail -n 1)
GET_JSON=$(echo "$GET_BODY" | sed '$d')

if [[ "$GET_CODE" != "200" ]]; then
  echo "GET /events/${EVENT_ID} expected 200, got ${GET_CODE}: ${GET_JSON}"
  exit 1
fi
echo "GET /events/${EVENT_ID} -> 200"

PG_COUNT=$(docker exec postgres psql -U relay -d relaydb -tAc \
  "SELECT count(*) FROM events WHERE event_id = '${EVENT_ID}';")
if [[ "$PG_COUNT" != "1" ]]; then
  echo "Postgres: expected 1 row for ${EVENT_ID}, got ${PG_COUNT}"
  exit 1
fi
echo "Postgres: row present"

KAFKA_HIT=$(docker exec kafka timeout 8 kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic events.business \
  --from-beginning \
  --property print.key=true \
  2>/dev/null | grep "${EVENT_ID}" | head -n 1 || true)
if [[ -z "$KAFKA_HIT" ]]; then
  echo "Kafka: no message with key/id ${EVENT_ID} on events.business"
  exit 1
fi
echo "Kafka: message present on events.business"

echo "Week 2 smoke OK"
