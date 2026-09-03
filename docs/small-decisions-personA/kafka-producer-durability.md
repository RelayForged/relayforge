# Decision: Producer acks, idempotence, and a locked lazy singleton

- **Context:** `docs/small-decisions-personA/kafka-producer-wrapper.md` established `publish()` and a lazy `AIOKafkaProducer` singleton. The audit found two durability gaps in that singleton. `producer.py` is otherwise unchanged.

- **Decision 1 — `acks="all"` and `enable_idempotence=True`:** set on construction in `_get_producer()`.
  - **Why:** aiokafka defaults to `acks=1`, which acknowledges once the partition leader has written, before replication. A leader failover in that window silently drops a message the API already accepted with **202** — precisely the "202 only if durable" contract from ADR 0002 and `fastapi-ingestion-app.md`. `enable_idempotence` prevents an internal producer retry from writing a duplicate record, which matters more now that the Dispatcher republishes retries.

- **Decision 2 — `max_in_flight_requests_per_connection` is not set:** attempted, then removed.
  - **Why:** It is a kafka-python option; `AIOKafkaProducer.__init__` rejects it with `TypeError`. aiokafka manages in-flight requests internally and its idempotent producer already constrains ordering. Recorded because it looks like a reasonable companion setting and is not.

- **Decision 3 — Guard the lazy singleton with an `asyncio.Lock`:** `_get_producer_lock()` mirrors the once-per-process lock pattern already used in `admin.ensure_topics()`, with a re-check of `_producer` inside the lock.
  - **Why:** There are two `await` points between the `if _producer is None` check and the assignment (`ensure_topics()` and `producer.start()`). Concurrent first-time callers — now realistic, since the Dispatcher publishes retries from parallel tasks — each constructed and started a producer, and one was orphaned: never flushed, never stopped, its buffered records lost. The lock is created lazily rather than at import so it binds to the running loop.
