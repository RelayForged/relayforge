# ADR 0014: Enforce DeliveryAttempt and Telemetry Immutability with a Database Trigger

**Status:** Accepted

**Context**

ADR 0007 states that DeliveryAttempt and Telemetry tables are append-only — no rows are updated after creation. ADR 0010 freezes the Pydantic models. ADR 0011 already enforces this at the PostgreSQL level for `events` via `prevent_events_mutation()`.

Retries are new `DeliveryAttempt` rows (`docs/architecture.md`: "Previous attempts remain immutable"). Telemetry is an ERI observation keyed by `attempt_id`; rewriting it would corrupt the sliding window Person B consumes.

`Delivery` is different: it is a lifecycle aggregate (`PENDING` → `SUCCESS` | `FAILED` | `DEAD_LETTER`). Retry Manager must mark terminal status via `model_copy` + `save()`. An UPDATE trigger on `deliveries` would make that impossible.

**Decision**

- `delivery_attempts` and `telemetry` get a `BEFORE UPDATE OR DELETE` trigger calling `prevent_append_only_mutation()` (shared function; events keep `prevent_events_mutation()` from ADR 0011). Created in Alembic revision `c026fc304e9b`.
- `deliveries` has **no** append-only trigger. Uniqueness is `(event_id, subscription_id)`.
- Repository `save()` on Attempt/Telemetry uses `ON CONFLICT DO NOTHING` (never `DO UPDATE`).

**Consequences**

Positive:
- Attempt history and ERI observations cannot be rewritten by application code, migrations, or ad-hoc SQL.
- Failures are loud: `delivery_attempts table is append-only: UPDATE is not permitted`.
- Delivery status transitions remain possible without dropping a trigger.

Negative:
- Completing an in-flight attempt cannot `UPDATE` completion columns. The Worker inserts the attempt **once**, with outcome fields populated, after HTTP (Dispatcher durability is the `deliveries` row). Retries insert a new `attempt_id` / `attempt_number`.
- Repairing a corrupt attempt/telemetry row requires temporarily dropping the trigger — an explicit, auditable action.
