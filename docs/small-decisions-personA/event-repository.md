# Decision: Event Repository — Async, Idempotent Save, Shared Engine

- **Context:** Step 4 required `save(event: Event) -> None` and `get_by_id(event_id: str) -> Event | None` on `EventRepository`, mapping between the frozen Pydantic `Event` and the `events_table` (SQLAlchemy Core) from Step 3.

- **Decision 1 — Async, not sync:** Implemented both methods as `async def` using SQLAlchemy's `AsyncEngine`/`asyncpg`, not sync `psycopg`.
  - **Why:** ADR 0006 mandates an async-native FastAPI ingestion path. `POST /events` (Week 2) will `await` this repository directly; a sync repository would block the event loop under concurrent load. Alembic migrations (Step 3) intentionally stay on the sync `psycopg` driver via `get_sync_database_url()` — migrations are a one-off CLI operation, not a hot path, so there's no reason to complicate `env.py` with an async migration runner.

- **Decision 2 — Idempotent `save()` via `ON CONFLICT (event_id) DO NOTHING`:** Used `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_nothing(index_elements=["event_id"])` instead of a plain `INSERT`.
  - **Why:** ADR 0002 (at-least-once delivery) means a caller may retry an operation that already partially succeeded. If `save()` is ever called twice with the same `event_id` (e.g. a caller retries after a Kafka publish failure that happened *after* the Postgres write succeeded), a plain `INSERT` would raise a unique-violation. `ON CONFLICT DO NOTHING` makes the repeat call a safe no-op instead of a crash, without violating append-only semantics (ADR 0011) — it never updates the existing row, it just skips inserting a duplicate.

- **Decision 3 — Shared engine factory (`engine.py`) instead of inlining engine creation in the repository:** Added `relay/infrastructure/persistence/engine.py` with a lazily-created, process-wide `AsyncEngine` via `get_engine()`.
  - **Why:** Week 2 adds `Endpoint` and `Subscription` repositories that need the exact same connection pool. A shared factory avoids each repository creating its own engine (which would exhaust Postgres connections). Repositories still accept `engine: AsyncEngine | None` in `__init__` (defaulting to `get_engine()`) rather than importing a global directly, so tests can inject an isolated engine.

**Verification:** Manually exercised `save()` (fresh insert), `save()` called twice with the same `Event` (idempotent, no error), `get_by_id()` round-tripping all fields including unwrapping/rewrapping `CorrelationID`/`CausationID`, and `get_by_id()` on a nonexistent ID returning `None` — all passed against the live Postgres container. Table reset to empty afterward via `alembic downgrade base && alembic upgrade head`.
