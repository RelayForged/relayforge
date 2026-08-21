# Decision: Endpoint/Subscription Schema and Repository Design

- **Context:** Week 2 Step 1 required Alembic migrations + repositories (`save`/`get_by_id`) for `Endpoint` and `Subscription`, following the `EventRepository` pattern from Week 1.

- **Decision 1 — No append-only trigger (contrast with ADR 0011):** `events` got a DB-level immutability trigger because `Event` is a business fact that never changes. `Endpoint`/`Subscription` are configuration entities with documented lifecycles (`docs/architecture.md`: Subscription `CREATED → ACTIVE → PAUSED → DISABLED → ARCHIVED`; Endpoint `is_active` toggled by future `EndpointPaused`/`EndpointResumed` handling). No trigger was added to these two tables.

- **Decision 2 — `save()` is an upsert (`ON CONFLICT ... DO UPDATE`), not insert-only:** `EventRepository.save()` uses `ON CONFLICT DO NOTHING` because a duplicate `Event` should be silently ignored. For `Endpoint`/`Subscription`, ADR 0010 mandates entities stay frozen and are "updated" via `entity.model_copy(update={...})` + re-persisting. For that pattern to actually work, `save()` must overwrite the existing row's mutable fields on conflict, not skip them.
  - `EndpointRepository.save()`: updates `target_url`, `owner_id`, `is_active`; leaves `created_at` untouched (creation timestamp is immutable even though config isn't).
  - `SubscriptionRepository.save()`: updates `event_types`, `status`; leaves `endpoint_id` and `created_at` untouched (a subscription doesn't get re-pointed at a different endpoint by an update; that would be a new subscription).
  - **Why:** No `update()` method exists in the sprint spec for these entities either — reusing `save()` for both create and update, gated by identity (`endpoint_id`/`subscription_id`), avoids inventing a second write method while staying consistent with the "frozen + model_copy" discipline the whole codebase follows.

- **Decision 3 — Column types:**
  - `Endpoint.owner_id` and `target_url` → `String`, not `UUID`. Unlike `event_id`/`correlation_id` (always `str(uuid4())` by construction), `owner_id` is an external identifier with no guarantee of UUID format, and `target_url` is obviously not a UUID.
  - `Subscription.event_types` (`List[str]`) → Postgres native `ARRAY(String)`, not `JSONB`. It's a flat list of strings with no nested structure, so a native array is simpler to query/index (e.g. `event_types @> ARRAY['OrderCreated']`) than unpacking JSON.
  - `subscriptions.endpoint_id` → `UUID` with a `ForeignKey("endpoints.endpoint_id")` constraint — enforced at the DB level, not just application logic.

**Verification:** Live-tested against the running Postgres container: Endpoint create + fetch round-trip; Endpoint upsert via `model_copy(update={"is_active": False})` correctly toggled the flag while preserving `created_at`; Subscription create + fetch round-trip (FK satisfied); Subscription upsert via `model_copy` correctly transitioned `status` to `PAUSED` and updated `event_types`; not-found returns `None` for both; and an orphan `Subscription` referencing a nonexistent `endpoint_id` correctly raised `IntegrityError` (FK enforced). Reset via `alembic downgrade base && upgrade head` afterward — both directions ran cleanly across both migrations.
