# Decision: Kafka Producer Wrapper Interface

- **Context:** Step 5 required a thin wrapper in `relay/infrastructure/messaging/` exposing `publish(topic: str, key: str, value: bytes) -> None`, and the ingestion API must never import `aiokafka` directly (ADR 0001).
- **Decision:**
  - Used `aiokafka.AIOKafkaProducer`, async, matching `EventRepository`'s async interface (Step 4) — both are awaited from the same future ingestion path.
  - Producer is a lazily-started, process-wide singleton (`_get_producer()`), mirroring the `engine.py` pattern from the persistence layer — avoids reconnecting to the broker on every `publish()` call.
  - Added `close()` for graceful shutdown (flush in-flight sends, stop the client) — needed later when wiring FastAPI's lifespan/shutdown hook.
  - Added a sibling `config.py` (mirrors `persistence/config.py`) for `KAFKA_BOOTSTRAP_SERVERS`, defaulting to `localhost:9092` (the `PLAINTEXT_HOST` listener in `docker-compose.yml`).
  - `key` is encoded as UTF-8 bytes inside the wrapper — callers pass a plain string (`event_id`), never raw bytes for the key.
- **Why:** Consistent with the existing persistence-layer conventions (lazy singleton engine/client, env-var config with docker-compose-matching defaults) established in Steps 3–4, so the same shape is easy to extend to a future consumer wrapper (Week 3–4 Dispatcher) without re-deriving conventions.

**Verification:** Called `publish("events.business", key="test-key-123", value=b'{"hello": "world"}')` against the live Kafka container. Topic did not exist yet; the broker auto-created it. Confirmed via `kafka-console-consumer` that the exact key and value landed on `events.business`.
