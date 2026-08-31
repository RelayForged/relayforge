# Decision: Delivery / DeliveryAttempt / Telemetry Schema and Repositories

- **Context:** Week 3 Phase 1 required Alembic + repositories mapping frozen kernel `Delivery`, `DeliveryAttempt`, and `Telemetry` (no kernel edits), plus `SubscriptionRepository.list_active_by_event_type`.

- **Decision 1 — `Delivery.save()` is idempotent on `(event_id, subscription_id)`, with a guarded status update:** `ON CONFLICT (event_id, subscription_id) DO UPDATE SET status = EXCLUDED.status WHERE deliveries.delivery_id = EXCLUDED.delivery_id`.
  - **Why:** Dispatcher Kafka redelivery may construct a new `delivery_id` for the same pair — `WHERE` fails, insert is a no-op (one Delivery per fan-out target). Retry Manager re-saves the *same* `delivery_id` with `model_copy(update={"status": "FAILED"})` — `WHERE` matches, status is written. `created_at` / FKs are never overwritten. This is not an Event-style `DO NOTHING`: Delivery is a lifecycle aggregate (ADR 0014).

- **Decision 2 — Attempt and Telemetry `save()` are insert-only (`ON CONFLICT DO NOTHING`):** PK conflict is a no-op. DB triggers reject UPDATE/DELETE (ADR 0014).
  - **Why:** ADR 0007: no rows updated after creation. Worker writes a completed `DeliveryAttempt` once (outcome fields set); each retry is a new row (`attempt_number` unique per `delivery_id`). Telemetry is 1:1 with that attempt.

- **Decision 3 — Telemetry PK is `attempt_id`, not a generated `telemetry_id`:** Kernel `Telemetry` has no own id; `attempt_id` references the attempt (ERI input). No extra jsonb column — none of the three kernel shapes carry a dict (ADR 0007's jsonb rule applies where the domain has JSON, e.g. `events.payload`).
  - **Why:** "Do not invent a fourth telemetry shape." `list_by_endpoint_id` ordered by `observed_at` for the later ERI window. `list_by_delivery_id` ordered by `attempt_number` for Retry Manager.

- **Decision 4 — `list_active_by_event_type` uses ARRAY contains:** `status = 'ACTIVE' AND event_types @> ARRAY[:event_type]` (`column.contains([event_type])`). No schema change on `subscriptions`.
  - **Why:** Week 2 stored `event_types` as `VARCHAR[]` specifically so this query stays a native array containment check rather than unpacking JSONB.
