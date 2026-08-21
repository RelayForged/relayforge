# Data Plane — Sequence Diagrams

Runtime behavior for Week 1 Steps 2–6 and Week 2 registration routes,
reflecting the actual implementation (not the plan). See
`docs/data-plane-class-diagram.md` for the static structure these sequences
operate on.

---

## 1. Docker Compose stack startup (Step 2)

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant Compose as docker compose
    participant Kafka
    participant Postgres
    participant Redis

    Dev->>Compose: docker compose up -d
    par
        Compose->>Kafka: create + start (KRaft, PLAINTEXT_HOST:9092)
        Kafka-->>Compose: healthcheck: kafka-broker-api-versions OK
    and
        Compose->>Postgres: create + start (relay/relaydb, :5432)
        Postgres-->>Compose: healthcheck: pg_isready OK
    and
        Compose->>Redis: create + start (:6379)
        Redis-->>Compose: healthcheck: redis-cli ping OK
    end
    Compose-->>Dev: all services healthy
```

---

## 2. Alembic migration — create `events` table (Step 3)

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant CLI as alembic CLI
    participant Env as migrations/env.py
    participant Config as persistence/config.py
    participant Models as persistence/models.py
    participant Migration as versions/ad1fc8a0fdb0_*.py
    participant PG as Postgres

    Dev->>CLI: alembic upgrade head
    CLI->>Env: run_migrations_online()
    Env->>Config: get_sync_database_url()
    Config-->>Env: postgresql+psycopg://... (or DATABASE_URL env)
    Env->>Models: metadata (target_metadata)
    Env->>PG: connect (psycopg, sync)
    Env->>Migration: upgrade()
    Migration->>PG: CREATE TABLE events (...)
    Migration->>PG: CREATE INDEX ix_events_correlation_id / ix_events_event_type
    Migration->>PG: CREATE FUNCTION prevent_events_mutation()
    Migration->>PG: CREATE TRIGGER events_immutable BEFORE UPDATE OR DELETE
    PG-->>Migration: OK
    Migration-->>Env: done
    Env-->>CLI: recorded in alembic_version
    CLI-->>Dev: "Running upgrade -> ad1fc8a0fdb0"
```

---

## 3. `EventRepository.save()` (Step 4)

```mermaid
sequenceDiagram
    participant Caller
    participant Repo as EventRepository
    participant Engine as persistence/engine.py
    participant SA as SQLAlchemy Core
    participant PG as Postgres (events + trigger)

    Caller->>Repo: await save(event)
    Repo->>Engine: get_engine()
    Engine-->>Repo: AsyncEngine (created on first call, reused after)
    Repo->>Repo: unwrap event.correlation_id.value,<br/>event.causation_id.value
    Repo->>SA: pg_insert(events_table).values(...)<br/>.on_conflict_do_nothing(event_id)
    Repo->>PG: async with engine.begin(): execute INSERT
    alt event_id not already present
        PG-->>Repo: 1 row inserted
    else event_id already present (retry)
        PG-->>Repo: 0 rows affected (no-op, no error)
    end
    Repo-->>Caller: None
```

---

## 4. `EventRepository.get_by_id()` (Step 4)

```mermaid
sequenceDiagram
    participant Caller
    participant Repo as EventRepository
    participant PG as Postgres

    Caller->>Repo: await get_by_id(event_id)
    Repo->>PG: SELECT * FROM events WHERE event_id = :id
    alt row found
        PG-->>Repo: row
        Repo->>Repo: CorrelationID(value=row.correlation_id)<br/>CausationID(value=row.causation_id)
        Repo->>Repo: Event(event_id=row.event_id, ...)
        Repo-->>Caller: Event instance
    else no row
        PG-->>Repo: empty result
        Repo-->>Caller: None
    end
```

---

## 5. Kafka `publish()` (Step 5)

```mermaid
sequenceDiagram
    participant Caller
    participant Wrapper as messaging/producer.py
    participant Config as messaging/config.py
    participant AIOKafka as AIOKafkaProducer
    participant Kafka

    Caller->>Wrapper: await publish(topic, key, value)
    Wrapper->>Wrapper: _get_producer()
    alt producer not started yet
        Wrapper->>Config: get_kafka_bootstrap_servers()
        Config-->>Wrapper: localhost:9092 (or env var)
        Wrapper->>AIOKafka: AIOKafkaProducer(bootstrap_servers=...)
        Wrapper->>AIOKafka: await start()
    end
    Wrapper->>Wrapper: key.encode("utf-8")
    Wrapper->>AIOKafka: await send_and_wait(topic, key, value)
    AIOKafka->>Kafka: produce record
    alt topic does not exist
        Kafka->>Kafka: auto-create topic
    end
    Kafka-->>AIOKafka: ack
    AIOKafka-->>Wrapper: ok
    Wrapper-->>Caller: None
```

---

## 6. Full Week 1 smoke script — `Event → Postgres → Kafka` (Step 6)

This is the Week 1 stop line: the composition of diagrams 3 and 5, driven
by `scripts/publish_smoke.py`.

```mermaid
sequenceDiagram
    participant Script as publish_smoke.py
    participant Event as Event (Pydantic)
    participant Repo as EventRepository
    participant PG as Postgres
    participant Wrapper as producer.py
    participant Kafka

    Script->>Event: CorrelationID(), CausationID(value=...)
    Script->>Event: Event(correlation_id, causation_id,<br/>event_type="OrderCreated", payload={...})
    Event-->>Script: frozen Event instance (event_id, occurred_at auto-generated)

    Script->>Repo: await save(event)
    Repo->>PG: INSERT ... ON CONFLICT DO NOTHING
    PG-->>Repo: row persisted
    Repo-->>Script: None

    Note over Script,PG: Persist-first guarantee (ADR 0002):<br/>Kafka publish only happens after Postgres commit succeeds.

    Script->>Event: event.model_dump_json().encode("utf-8")
    Script->>Wrapper: await publish("events.business",<br/>key=event.event_id, value=json_bytes)
    Wrapper->>Kafka: send_and_wait(...)
    Kafka-->>Wrapper: ack
    Wrapper-->>Script: None

    Script->>Wrapper: await close()
    Wrapper->>Kafka: producer.stop()

    Script->>Script: print(f"event_id={event.event_id}")
```

**Verified live** (see commit `4bb5cee`): the row in Postgres and the message
on `events.business` were independently confirmed to contain matching data
for the same `event_id`.

---

## 7. Register Endpoint then Subscription (Week 2)

`POST /endpoints` then `POST /subscriptions`. Kafka is not on this path —
these are configuration writes only. The subscription route looks up the
endpoint before insert so a missing `endpoint_id` returns 404 instead of a
raw FK error.

```mermaid
sequenceDiagram
    participant User
    participant App as FastAPI (relay.api)
    participant EPRepo as EndpointRepository
    participant SubRepo as SubscriptionRepository
    participant PG as Postgres

    User->>App: POST /endpoints<br/>{target_url, owner_id}
    App->>App: Endpoint(target_url, owner_id)<br/>is_active=True
    App->>EPRepo: await save(endpoint)
    EPRepo->>PG: UPSERT endpoints (ON CONFLICT DO UPDATE)
    PG-->>EPRepo: row persisted
    EPRepo-->>App: None
    App-->>User: 201 Endpoint

    User->>App: POST /subscriptions<br/>{endpoint_id, event_types}
    App->>EPRepo: await get_by_id(endpoint_id)
    EPRepo->>PG: SELECT * FROM endpoints WHERE endpoint_id = :id
    alt endpoint exists
        PG-->>EPRepo: row
        EPRepo-->>App: Endpoint
        App->>App: Subscription(endpoint_id, event_types,<br/>status="ACTIVE")
        App->>SubRepo: await save(subscription)
        SubRepo->>PG: UPSERT subscriptions<br/>(FK endpoint_id → endpoints)
        PG-->>SubRepo: row persisted
        SubRepo-->>App: None
        App-->>User: 201 Subscription
    else endpoint missing
        PG-->>EPRepo: empty
        EPRepo-->>App: None
        App-->>User: 404 Endpoint not found
    end
```
