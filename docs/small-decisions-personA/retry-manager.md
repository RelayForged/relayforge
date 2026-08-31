# Decision: Retry Manager executes policy fields, delay lives on Kafka

- **Context:** Phase 5: after a failed Worker attempt, schedule the next `DeliveryAttempt` from `DeliveryPolicy.retry_policy` / `isolation_policy`, publish `events.internal.delivery`, or mark Delivery FAILED. No `asyncio.sleep` in the Worker or Retry Manager.

- **Decision 1 — Exponential delay from policy fields only:** `delay = min(initial_delay_seconds * backoff_multiplier ** (attempt_number - 1), max_delay_seconds)`. After attempt 1 → 5s, after 2 → 10s, … cap 300s. Retry while `attempt_number < max_retries` (10 attempts total). Do not call `StrategyA.evaluate()` here.
  - **Why:** Retry Manager executes a policy already produced by Strategy A. Re-running Strategy A would be strategy selection, which this sprint forbids in the data plane.

- **Decision 2 — Isolation PAUSE/QUARANTINE does not mark FAILED:** no new attempt, no Kafka publish, Delivery stays PENDING (durable). Exhausted retries → `model_copy(update={"status": "FAILED"})`. Success → `SUCCESS`.
  - **Why:** Workspace rule: PAUSE/QUARANTINE must not HTTP and must leave events durable. Collapsing those into FAILED would drop work.

- **Decision 3 — `RetryWork` payload carries `next_attempt_at`:** published with key=`event_id` via the existing `publish()` wrapper. Dispatcher commits the retry offset (attempt already in Postgres), then `create_task` waits until `next_attempt_at` before HTTP. Worker path only calls `RetryManager.handle` after Telemetry save.
  - **Why:** Kafka has no delayed-topic API. Sleep must not sit in the Worker. A background wait on the Dispatcher keeps the consumer loop free for other events.
