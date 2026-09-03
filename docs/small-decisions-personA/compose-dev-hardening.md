# Decision: docker-compose is an explicitly dev-only harness, with a durable Kafka log

- **Context:** The audit found the Compose stack destroyed the Kafka log on any container recreation and published three unauthenticated services on all interfaces. No app services are added (out of scope for this sprint).

- **Decision 1 — Kafka gets a named volume; `KAFKA_LOG_DIRS` moves to `/var/lib/kafka/data`:** new `kafka_data` volume alongside the existing `postgres_data`.
  - **Why:** `KAFKA_LOG_DIRS` pointed at `/tmp/kraft-combined-logs` **inside the container** with no volume, so `docker compose down` or any recreation wiped the entire log — including persisted-but-unconsumed `events.business` records and every scheduled retry on `events.internal.delivery`. ADR 0002's "re-deliver from the last committed Kafka offset" is meaningless if the log is ephemeral, and ADR 0015's redelivery-on-crash guarantee depends on it directly. Changing the log dir resets the local broker once, on purpose.

- **Decision 2 — All published ports bound to `127.0.0.1`; `29092` no longer published:** `9092`, `5432`, `6379` become loopback-only.
  - **Why:** Kafka runs `PLAINTEXT` with no SASL, and Postgres uses the dev credentials committed in this file, so binding to `0.0.0.0` exposed unauthenticated read/write on the durable log and the database to the whole network. `29092` is the in-network broker listener that only sibling containers use, so publishing it was never needed. `KAFKA_ADVERTISED_LISTENERS` is unchanged, so `KAFKA_BOOTSTRAP_SERVERS=localhost:9092` still works from the host.

- **Decision 3 — Credentials via `${VAR:-default}`, not literals:** `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` and a new `REDIS_PASSWORD`.
  - **Why:** Overridable without editing the file. Dev defaults are deliberately *kept* rather than made mandatory: `scripts/api_smoke.sh` runs `docker exec postgres psql -U relay -d relaydb`, and requiring an env var would break the documented one-command dev flow. The real production guard is in `relay/infrastructure/persistence/config.py`, which refuses to fall back to these credentials outside `RELAY_ENV=dev` — see `env-and-packaging.md`.

- **Decision 4 — Redis gets `requirepass`:** via `command:`, with the healthcheck updated to authenticate.
  - **Why:** It was running with no authentication at all. Redis is not wired until Week 5–6 (three-tier `PolicyCache`), but an open instance on a published port is a liability regardless of whether our code connects to it.

- **Decision 5 — Drop the `version:` key:** replaced by a header comment stating the file is a dev harness.
  - **Why:** Obsolete and ignored by Compose v2, which warns about it. The comment matters more than the key did: every trade-off above assumes this stack is never the production topology.

- **Not done:** per-service `deploy.resources.limits`, and app services (API / Dispatcher / Simulator) with `depends_on: condition: service_healthy`. Extra Compose services are explicitly out of scope this sprint, so the existing healthchecks still have no consumer and startup ordering remains manual.
