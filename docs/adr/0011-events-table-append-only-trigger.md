# ADR 0011: Enforce Events Table Immutability with a Database Trigger

**Status:** Accepted

**Context**

ADR 0002 (at-least-once delivery) and ADR 0010 (frozen Pydantic domain models) both establish that `Event` is immutable — a business fact that is never modified after publication. `docs/architecture.md` states the `events` table (and other operational tables) must be append-only.

At the application layer, this is already enforced structurally: the `Event` Pydantic model is `frozen=True`, and the repository (Step 4) only exposes `save()` and `get_by_id()` — no `update()` method exists. However, this only prevents *this codebase's* repository from mutating events. It does not prevent:

- A future contributor adding an `update_event()` method without realizing the constraint.
- A manual `UPDATE`/`DELETE` run directly against the database (migration script, admin tooling, ad-hoc debugging).
- A bug in a future service that has direct DB access bypassing the repository.

Relying purely on application-layer discipline conflicts with the project's own engineering principle: "Explicit over implicit" and "every failure must produce knowledge" (`docs/architecture.md` §Engineering Standards). An accidental mutation of event history would corrupt the audit trail and violate at-least-once delivery guarantees silently — no exception, no log, just quietly wrong data.

**Decision**

Enforce append-only semantics for the `events` table at the PostgreSQL level using a `BEFORE UPDATE OR DELETE` trigger (`events_immutable`) that raises an exception via a `prevent_events_mutation()` trigger function. This is created in the same migration that creates the `events` table (`ad1fc8a0fdb0_create_events_table.py`) and reversed in its `downgrade()`.

Any `UPDATE` or `DELETE` against `events` — from the application, a migration, or a manual `psql` session — now fails loudly with `events table is append-only: UPDATE is not permitted` instead of silently succeeding.

**Consequences**

Positive:
- Immutability is guaranteed by the database, not just by convention or code review. This closes the gap between "the architecture says events are immutable" and "the schema actually enforces it."
- Failures are observable: any accidental mutation attempt raises a clear PostgreSQL exception rather than corrupting data silently.
- No application code changes required — the trigger is transparent to correct usage (`INSERT` + `SELECT` only).

Negative:
- A genuine future need to fix corrupted/malformed event rows (e.g., a data-repair script after a bug) cannot use `UPDATE`/`DELETE` directly — the trigger would need to be temporarily dropped, which is an explicit, auditable, deliberate action rather than an accidental one. This is treated as a feature, not a limitation: correcting immutable history should be hard to do by accident.
- Adds a small amount of migration complexity (raw SQL via `op.execute`) instead of pure SQLAlchemy Core operations.

This same pattern (trigger-enforced append-only) should be considered for other immutable tables introduced later (`DeliveryAttempt`, `PolicyEvaluation`, `ExplainabilityRecord`) per `docs/architecture.md` §Engineering Standards, though that decision is deferred to whoever implements those tables (Person A for `DeliveryAttempt` in Week 3–4, Person B for `PolicyEvaluation`/`ExplainabilityRecord`).
