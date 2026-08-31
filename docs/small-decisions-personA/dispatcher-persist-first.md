# Decision: Dispatcher persist-first, Strategy A, in-memory worker queue

- **Context:** Week 3 Phase 3 requires a Kafka Dispatcher that fans out each `events.business` Event to ACTIVE subscriptions, persists `Delivery` + initial `DeliveryAttempt`, evaluates Strategy A, then hands off to a worker without HTTP.

- **Decision 1 — Commit offset after all fan-out persists, not after HTTP:** `handle_message` parses the Event, persists every subscription's Delivery/Attempt, evaluates + enqueues, *then* `consumer.commit(message)`.
  - **Why:** ADR 0002 persist-first. Auto-commit is off in the Phase 2 wrapper. If Postgres fails, the offset is not advanced and Kafka redelivers. HTTP is Phase 4 and must not gate the commit.

- **Decision 2 — One Delivery per `(event_id, subscription_id)`, not per Event:** After `save()`, reload via `get_by_event_and_subscription` so a redelivery reuses the canonical `delivery_id` (new uuid4 would no-op the insert and then FK-fail the attempt). Reuse existing `attempt_number=1` if present.
  - **Why:** Unique `(event_id, subscription_id)` plus at-least-once Kafka. Attempt 1 is unique per delivery; inserting a second attempt 1 would violate `uq_delivery_attempts_delivery_id_attempt_number`.

- **Decision 3 — Strategy A only; `computed_at` passed in:** `StrategyA().evaluate(endpoint_id, EndpointState.AVAILABLE, ERIScore.perfect(window_size=0, computed_at=...), computed_at)`. `PAUSE` / `QUARANTINE` skip enqueue; rows remain. `FeatureFlags.from_env()` is read but not used to select B/C this sprint.
  - **Why:** Week 3–4 ignore live AEDE. Strategy A ignores ERI/state; a perfect ERI is the "no window yet" input. Isolation is still honored so later strategies cannot accidentally HTTP.

- **Decision 4 — In-memory `asyncio.Queue` + no-op `enqueue_worker`:** Queue holds `(DeliveryAttempt, DeliveryPolicy)`. No httpx / Simulator call.
  - **Why:** Phase 3 explicitly does not build worker HTTP. Phase 4 attaches the worker to this queue.
