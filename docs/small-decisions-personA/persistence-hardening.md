# Decision: Persistence query shape, indexes, and status constraints

- **Context:** The audit measured ~9–10 separate transactions per delivery, a sequential scan on every ingested event, an unbounded telemetry read, and free-form status strings. Alembic revision `b7c41de9a052` plus repository changes address these. The `delivery_attempts` trigger change in the same revision is ADR 0019.

- **Decision 1 — GIN index for the fan-out predicate:** `ix_subscriptions_event_types_gin` on `subscriptions.event_types`.
  - **Why:** `list_active_by_event_type` filters `event_types @> ARRAY[event_type]`, which a btree cannot serve, so **every** ingested event sequentially scanned `subscriptions` — O(events x subscriptions). Verified with `EXPLAIN` on 20k rows: `Bitmap Index Scan` with the index, `Seq Scan` without it. A partial `WHERE status = 'ACTIVE'` variant was considered and dropped: the planner already filters status cheaply after the bitmap scan, and a second overlapping GIN index costs write bandwidth for no measured gain.

- **Decision 2 — `save()` returns the stored row via `RETURNING`:** `DeliveryRepository.save` and `DeliveryAttemptRepository.save` return `Optional[<entity>]`; `None` means the upsert was a deliberate no-op.
  - **Why:** The Dispatcher used to `save()` then immediately re-`SELECT` to learn the canonical `delivery_id`, on every fan-out target. `RETURNING` collapses that to one round trip, with the read-back kept only for the redelivery case where the conflicting row has a different `delivery_id` and the `WHERE` clause suppresses the update. Callers that ignore the return value are unaffected.

- **Decision 3 — `insert_if_absent()` as a separate write path:** conflicts on `(delivery_id, attempt_number)` with `DO NOTHING ... RETURNING`; `save()` keeps conflicting on `attempt_id`.
  - **Why:** Postgres `ON CONFLICT` takes one target, and the two callers need different ones — the Worker completes an attempt it already holds by `attempt_id`, while the Dispatcher and Retry Manager claim a slot by `(delivery_id, attempt_number)`. Merging them was not possible: a claim carries a fresh `attempt_id`, so an `attempt_id`-targeted upsert would never fire and would instead raise `IntegrityError` on the unique constraint. This is what let both callers stop reading every attempt for a delivery just to test for one row (`get_by_delivery_and_number` covers the rest, served directly by `uq_delivery_attempts_delivery_id_attempt_number`).

- **Decision 4 — Fan-out joins Subscription to Endpoint:** new `list_active_targets_by_event_type` returns `list[tuple[Subscription, Endpoint]]` and filters `endpoints.is_active`. Endpoint columns are selected under explicit `endpoint_*` labels. `list_active_by_event_type` is retained.
  - **Why:** One `EndpointRepository.get_by_id` per subscription was a textbook N+1 — N extra SELECTs per event, each on its own connection. Explicit labels are required because both tables define `endpoint_id` and `created_at`, so unlabelled `Row` attribute access would silently resolve to the subscriptions side. Filtering `is_active` here also fixed the separate bug that deactivated endpoints still received traffic. The old method stays because it is documented in the Week 3–4 handoff and may have consumers outside this package.

- **Decision 5 — `list_by_endpoint_id` is a bounded trailing window:** keyword-only `limit` (default 100), `ORDER BY observed_at DESC` + `LIMIT`, reversed in Python to preserve the oldest-first contract. Backed by `ix_telemetry_endpoint_id_observed_at` on `(endpoint_id, observed_at DESC)`, replacing the endpoint-only index.
  - **Why:** `telemetry` is append-only and grows once per attempt forever; the unbounded version materialized an endpoint's entire history and built one Pydantic object per row. ERI only ever needs the trailing window. The signature stays backward-compatible for Person B, and reversing in Python keeps the documented ordering while letting the index serve the limit.

- **Decision 6 — `CHECK` constraints on status columns:** `ck_deliveries_status` and `ck_subscriptions_status`, declared in both the migration and `models.py`.
  - **Why:** `Delivery.status` is a plain `str` on the frozen kernel entity, so a typo used to persist silently and strand the row outside every status query — the same class of gap ADR 0019 closes for attempt rows. Declaring them in `models.py` too keeps `target_metadata` from drifting on future autogenerate.

- **Decision 7 — Drop two redundant indexes:** `ix_deliveries_event_id` and `ix_delivery_attempts_delivery_id`.
  - **Why:** Each duplicates the leading column of an existing unique constraint (`uq_deliveries_event_id_subscription_id`, `uq_delivery_attempts_delivery_id_attempt_number`), which already serves leading-column lookups. They cost write bandwidth and buffer cache for no read benefit.

- **Decision 8 — The revision is verified reversible:** `alembic downgrade -1` then `upgrade head` round-trips cleanly, restoring the dropped indexes and the prior trigger.
  - **Why:** The revision drops indexes and creates constraints against pre-existing rows. Note that the `CHECK` constraints will fail to apply on a database already holding an out-of-domain status value; the dev database had none.
