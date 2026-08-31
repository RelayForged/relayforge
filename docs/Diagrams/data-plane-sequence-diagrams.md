# Data Plane — Sequence Diagrams

Runtime behavior for Week 1 Steps 2–6, Week 2 registration routes, and
Week 3–4 dispatch / delivery / retry, reflecting the actual implementation
(not the plan). See `docs/Diagrams/data-plane-class-diagram.md` for the
static structure these sequences operate on.

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

---

## 8. Dispatcher — `events.business` fan-out (Week 3)

`KafkaConsumer` yields a message. Dispatcher parses `Event`, loads ACTIVE
subscriptions for `event_type`, then per subscription persist-first
`Delivery` + initial `DeliveryAttempt`, then `StrategyA.evaluate()`. HTTP
runs only if isolation is not `PAUSE` / `QUARANTINE`. Offset is committed
after Postgres, not after HTTP.

```mermaid
sequenceDiagram
    participant Kafka
    participant Consumer as KafkaConsumer
    participant Disp as Dispatcher
    participant SubRepo as SubscriptionRepository
    participant DelRepo as DeliveryRepository
    participant AttRepo as DeliveryAttemptRepository
    participant Strat as StrategyA
    participant EPRepo as EndpointRepository
    participant Worker as WebhookWorker
    participant PG as Postgres

    Kafka->>Consumer: events.business (event.model_dump_json)
    Consumer->>Disp: KafkaMessage
    Disp->>Disp: Event.model_validate_json(message.value)
    Disp->>SubRepo: list_active_by_event_type(event.event_type)
    SubRepo->>PG: SELECT ... status=ACTIVE AND event_types contains type
    PG-->>SubRepo: rows
    SubRepo-->>Disp: list~Subscription~

    loop per ACTIVE Subscription
        Disp->>Disp: Delivery(event_id, subscription_id,<br/>endpoint_id, status=PENDING)
        Disp->>DelRepo: await save(delivery)
        DelRepo->>PG: INSERT deliveries<br/>(unique event_id + subscription_id)
        PG-->>DelRepo: row persisted
        Disp->>AttRepo: await save(DeliveryAttempt attempt_number=1)
        AttRepo->>PG: INSERT delivery_attempts
        PG-->>AttRepo: row persisted

        Note over Disp,PG: Persist-first (ADR 0002):<br/>HTTP is not required before the Kafka offset commit.

        Disp->>Strat: evaluate(endpoint_id,<br/>AVAILABLE, eri, computed_at)
        Strat-->>Disp: DeliveryPolicy<br/>(EXPONENTIAL 5s ×2 cap 300s max 10)
        alt isolation PAUSE / QUARANTINE
            Disp->>Disp: skip HTTP; Delivery stays durable
        else CONTINUE
            Disp->>EPRepo: get_by_id(delivery.endpoint_id)
            EPRepo-->>Disp: Endpoint (target_url)
            Disp->>Worker: execute(attempt, target_url,<br/>endpoint_id, delivery, policy)
        end
    end

    Disp->>Consumer: commit(message)
```

---

## 9. Webhook Worker then Retry Manager (Week 4)

Worker POSTs once to Simulator `POST /webhook` (no backoff). On 5xx it
persists the completed `DeliveryAttempt` and a `Telemetry` row, then
Retry Manager applies `retry_policy` fields and publishes the next
attempt to `events.internal.delivery`. Dispatcher later consumes that
topic, waits until `next_attempt_at`, and calls the worker again.

```mermaid
sequenceDiagram
    participant Disp as Dispatcher
    participant Worker as WebhookWorker
    participant Sim as Simulator POST /webhook
    participant AttRepo as DeliveryAttemptRepository
    participant TelRepo as TelemetryRepository
    participant Retry as RetryManager
    participant DelRepo as DeliveryRepository
    participant Wrapper as producer.py
    participant Consumer as KafkaConsumer
    participant Kafka
    participant PG as Postgres

    Disp->>Worker: execute(attempt, target_url, endpoint_id,<br/>delivery, policy)
    Worker->>Sim: HTTP POST (httpx, strict timeout)
    Sim-->>Worker: 5xx
    Worker->>Worker: attempt.model_copy<br/>(completed_at, http_status, success=False)
    Worker->>AttRepo: await save(completed)
    AttRepo->>PG: complete outcome columns<br/>(completed_at was null)
    PG-->>AttRepo: row updated
    Worker->>TelRepo: await save(Telemetry)
    TelRepo->>PG: INSERT telemetry<br/>ON CONFLICT (attempt_id) DO NOTHING
    PG-->>TelRepo: observation persisted
    Worker->>Retry: handle(completed, delivery, policy)

    alt success
        Retry->>DelRepo: save(status=SUCCESS)
        DelRepo->>PG: UPSERT deliveries.status
    else attempt_number greater or equal max_retries
        Retry->>DelRepo: save(status=FAILED)
        DelRepo->>PG: UPSERT deliveries.status
    else isolation PAUSE / QUARANTINE
        Retry->>Retry: no HTTP; Delivery stays PENDING
    else retry
        Retry->>Retry: delay = min(5 * 2^(n-1), 300)
        Retry->>AttRepo: save(DeliveryAttempt n+1)
        AttRepo->>PG: INSERT delivery_attempts
        Retry->>Wrapper: publish("events.internal.delivery",<br/>key=event_id, RetryWork JSON)
        Wrapper->>Kafka: send_and_wait
        Kafka-->>Wrapper: ack
    end

    Kafka->>Consumer: events.internal.delivery (RetryWork)
    Consumer->>Disp: KafkaMessage
    Disp->>Consumer: commit(message)
    Disp->>Disp: sleep until next_attempt_at
    Disp->>Worker: execute(next attempt, target_url, ...)
```
