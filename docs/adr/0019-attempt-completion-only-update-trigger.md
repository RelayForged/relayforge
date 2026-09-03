# ADR 0019: Restore DeliveryAttempt Immutability as a Completion-Only UPDATE Trigger

**Status:** Accepted

**Amends:** ADR 0014

**Context**

ADR 0014 gave `delivery_attempts` a `BEFORE UPDATE OR DELETE` trigger so "attempt history cannot be rewritten by application code, migrations, or ad-hoc SQL", and specified that `save()` uses `ON CONFLICT DO NOTHING`, never `DO UPDATE`.

Persist-first (ADR 0002) then forced a change. The Dispatcher must insert a started attempt *before* HTTP, and the Worker must record the outcome *after* it. That is inherently an UPDATE of the same row, so Alembic revision `6b999f7451a9` dropped UPDATE from the trigger, leaving DELETE-only.

That fix went too far. With UPDATE unguarded, *every* column became freely rewritable — `attempt_id`, `delivery_id`, `attempt_number`, `started_at`, and the outcome of an already-completed attempt. The only remaining protection was the `where completed_at IS NULL` clause in `DeliveryAttemptRepository.save()`. ADR 0014's guarantee was reduced to a convention in one Python method, which is exactly the property it was written to prevent. Ad-hoc SQL, a future migration, or a second repository could silently rewrite attempt history, and Person B's ERI window is derived from it.

The two requirements are not actually in conflict. Exactly one transition is legitimate: filling completion columns on a row whose `completed_at` is still null.

**Decision**

Replace the DELETE-only trigger with a row-level `BEFORE UPDATE` trigger, `prevent_attempt_identity_mutation()`, in Alembic revision `b7c41de9a052`. It raises when:

- `OLD.completed_at IS NOT NULL` — a completed attempt is immutable, full stop; or
- `attempt_id`, `delivery_id`, `attempt_number`, or `started_at` differ between `OLD` and `NEW`.

The DELETE trigger from ADR 0014 stays. Retries remain new rows with a new `attempt_id` / `attempt_number`.

Verified against live Postgres: the sanctioned completion write succeeds, and a second UPDATE of the same row fails with `delivery_attempts row <id> is already completed and is immutable`.

**Consequences**

Positive:
- ADR 0014's immutability guarantee is enforced by the database again, not by one method's `WHERE` clause.
- Persist-first still works: the Dispatcher inserts, the Worker completes, exactly once.
- Double-completion is now a database error rather than a silent overwrite, so an at-least-once redelivery cannot corrupt the ERI window.
- Identity columns are pinned, so an attempt can never be re-parented to a different Delivery.

Negative:
- Attempt-outcome columns are no longer settable a second time even deliberately. Repairing a wrong outcome requires dropping the trigger — explicit and auditable, as ADR 0014 already accepted for the append-only case.
- The trigger is a row-level PL/pgSQL function on a hot write path. The cost is negligible relative to the surrounding HTTP call.
- ADR 0014's statement that Attempt `save()` uses `ON CONFLICT DO NOTHING` is now incorrect. It is `DO UPDATE` on `attempt_id`, narrowed by `where completed_at IS NULL`, with the trigger as the backstop. This ADR is the authority on that point.

**Related**

`docs/small-decisions-personA/persistence-hardening.md` records the accompanying `CHECK` constraints on `deliveries.status` and `subscriptions.status`, which close the same class of gap for status values.
