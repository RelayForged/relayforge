# Decision: Kafka Healthcheck in Docker Compose

- **Context:** Step 2 required all three services (Kafka, Postgres, Redis) to be verifiably "healthy" via `docker compose ps`. The existing `docker-compose.yml` had healthchecks for Postgres and Redis but none for Kafka, so there was no automated way to confirm the broker was ready before dependent services (producer scripts, API) tried to connect.
- **Decision:** Added a `healthcheck` block to the `kafka` service using `kafka-broker-api-versions --bootstrap-server localhost:9092` (a CLI tool bundled in the `confluentinc/cp-kafka` image), with `interval: 10s`, `timeout: 10s`, `retries: 10`, `start_period: 30s`.
- **Why:**
  - KRaft-mode Kafka can take longer to report ready than a simple TCP port check would suggest; `start_period: 30s` avoids false-negative failures during startup.
  - Using the bundled `kafka-broker-api-versions` script avoids adding new tooling/images — it ships inside the existing `cp-kafka:7.4.0` image.
  - No new services were added; this only extends an existing service definition per the "do not add extra services" constraint.

## Local environment note (not a compose change)
On this machine, host port `5432` was already bound by native `postgresql` (v16 and v17) systemd services, which caused the `postgres` container to fail its first `docker compose up -d` (networking setup failed, container stuck in `Created`). Resolved by the user running `sudo systemctl stop postgresql`, then `docker compose up -d --force-recreate postgres` to rebind the port correctly. No changes were made to `docker-compose.yml` for this — it was purely a host environment conflict.
