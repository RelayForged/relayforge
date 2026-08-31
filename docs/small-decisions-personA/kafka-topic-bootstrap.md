# Decision: Kafka topic bootstrap via messaging AdminClient

- **Context:** Dispatcher subscribes to `events.internal.delivery` at startup. Broker auto-create only happens on produce, so a missing retry topic broke smoke. Creating topics with `docker exec kafka kafka-topics` in the smoke script is environment-specific and conflicts with anyone not using that container name.

- **Decision:** `relay/infrastructure/messaging/admin.py` `ensure_topics()` uses `AIOKafkaAdminClient` (aiokafka stays inside messaging/). It creates the four named topics if missing, once per process. Called from `KafkaConsumer.start()`, `producer._get_producer()`, and FastAPI lifespan. Smoke calls `await ensure_topics()` — no Docker. `KAFKA_TOPIC_PARTITIONS` / `KAFKA_TOPIC_REPLICATION_FACTOR` default to 1 for local Compose (single broker).
  - **Why:** Person B/C do not import this. Idempotent `list_topics` + create-missing + `TopicAlreadyExistsError` is safe if API and Dispatcher start together. Production sets RF via env; no extra Compose service.
