# Decision: Week 2 Smoke Script as Bash + curl

- **Context:** Sprint Step 9 specifies testing with `curl POST /events` and `curl GET /events/{id}`, plus verifying Postgres and Kafka.
- **Decision:** `scripts/api_smoke.sh` (bash + curl), not Python/httpx. If `BASE_URL` is down it starts `uvicorn relay.api.main:app` and stops it on exit.
- **Why:** Hits the real HTTP path (not in-process ASGI). Matches the sprint wording. One command from the repo root after `docker compose up -d`.
