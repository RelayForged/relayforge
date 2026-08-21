# Data Plane — UML Class Diagrams

Core structure of the Relay Data Plane, split by layer so each diagram stays readable.

Sources: `relay/kernel/entities/`, `relay/kernel/value_objects/identifiers.py`, `relay/infrastructure/persistence/`, `relay/api/`, Week 1–2 contracts in `docs/sprints/week-1-2.md`.

---

## Domain Model

Entities and value objects only (`relay/kernel/`). `Delivery` / `DeliveryAttempt` are kernel types for later dispatcher/worker work; they are not persisted in Weeks 1–2.

```mermaid
classDiagram
    direction LR

    class CorrelationID {
        <<frozen value object>>
        +value: str
    }

    class CausationID {
        <<frozen value object>>
        +value: str
    }

    class Event {
        <<frozen entity>>
        +event_id: str
        +correlation_id: CorrelationID
        +causation_id: CausationID
        +event_type: str
        +payload: Dict~str, Any~
        +occurred_at: datetime
    }

    class Endpoint {
        <<frozen entity>>
        +endpoint_id: str
        +target_url: str
        +owner_id: str
        +created_at: datetime
        +is_active: bool
    }

    class Subscription {
        <<frozen entity>>
        +subscription_id: str
        +endpoint_id: str
        +event_types: List~str~
        +created_at: datetime
        +status: str
    }

    class Delivery {
        <<frozen entity>>
        +delivery_id: str
        +event_id: str
        +subscription_id: str
        +endpoint_id: str
        +created_at: datetime
        +status: str
    }

    class DeliveryAttempt {
        <<frozen entity>>
        +attempt_id: str
        +delivery_id: str
        +attempt_number: int
        +started_at: datetime
        +completed_at: Optional~datetime~
        +http_status: Optional~int~
        +success: bool
        +error_message: Optional~str~
    }

    Event *-- CorrelationID : composition
    Event *-- CausationID : composition
    Endpoint "1" --> "*" Subscription : endpoint_id
    Event "1" --> "*" Delivery : event_id
    Subscription "1" --> "*" Delivery : subscription_id
    Endpoint "1" --> "*" Delivery : endpoint_id
    Delivery "1" --> "*" DeliveryAttempt : retries are new attempts
```

---

## Persistence Layer

Repositories, SQLAlchemy tables, and the engine/config they sit on. Repositories map frozen kernel entities ↔ table rows; nothing outside this package should import the tables.

```mermaid
classDiagram
    direction LR

    class DatabaseConfig {
        <<module functions>>
        +get_sync_database_url() str
        +get_async_database_url() str
    }

    class PersistenceEngine {
        <<module functions>>
        +get_engine() AsyncEngine
    }

    class EventRepository {
        <<append-only>>
        +save(event: Event) None
        +get_by_id(event_id: str) Event | None
    }

    class EndpointRepository {
        <<upsert>>
        +save(endpoint: Endpoint) None
        +get_by_id(endpoint_id: str) Endpoint | None
    }

    class SubscriptionRepository {
        <<upsert>>
        +save(subscription: Subscription) None
        +get_by_id(subscription_id: str) Subscription | None
    }

    class events_table {
        <<SQLAlchemy Table>>
        +event_id: UUID PK
        +correlation_id: UUID
        +causation_id: UUID
        +event_type: String
        +payload: JSONB
        +occurred_at: timestamptz
    }

    class endpoints_table {
        <<SQLAlchemy Table>>
        +endpoint_id: UUID PK
        +target_url: String
        +owner_id: String
        +created_at: timestamptz
        +is_active: Boolean
    }

    class subscriptions_table {
        <<SQLAlchemy Table>>
        +subscription_id: UUID PK
        +endpoint_id: UUID FK
        +event_types: ARRAY~String~
        +created_at: timestamptz
        +status: String
    }

    PersistenceEngine ..> DatabaseConfig : async URL
    EventRepository ..> PersistenceEngine : get_engine
    EventRepository ..> events_table : INSERT ON CONFLICT DO NOTHING
    EndpointRepository ..> PersistenceEngine : get_engine
    EndpointRepository ..> endpoints_table : UPSERT
    SubscriptionRepository ..> PersistenceEngine : get_engine
    SubscriptionRepository ..> subscriptions_table : UPSERT
    subscriptions_table --> endpoints_table : FK endpoint_id
```

---

## API and Messaging Layer

FastAPI app, routers, request/response DTOs, and the Kafka producer wrapper. Routers never import `aiokafka`. Persist-first: Events router saves via `EventRepository`, then `publish()`.

```mermaid
classDiagram
    direction LR

    class FastAPIApp {
        <<relay.api.main>>
        +lifespan() close producer
    }

    class EventsRouter {
        <<relay.api.events>>
        +ingest_event(body: IngestEventRequest) 202
        +get_event(event_id: str) Event
    }

    class EndpointsRouter {
        <<relay.api.endpoints>>
        +create_endpoint(body: CreateEndpointRequest) Endpoint
    }

    class SubscriptionsRouter {
        <<relay.api.subscriptions>>
        +create_subscription(body: CreateSubscriptionRequest) Subscription
    }

    class IngestEventRequest {
        <<API DTO>>
        +event_type: str
        +payload: Dict~str, Any~
    }

    class IngestEventResponse {
        <<API DTO>>
        +event_id: str
    }

    class CreateEndpointRequest {
        <<API DTO>>
        +target_url: str
        +owner_id: str
    }

    class CreateSubscriptionRequest {
        <<API DTO>>
        +endpoint_id: str
        +event_types: List~str~
    }

    class KafkaProducer {
        <<module functions>>
        +publish(topic: str, key: str, value: bytes) None
        +close() None
    }

    FastAPIApp *-- EventsRouter : include_router
    FastAPIApp *-- EndpointsRouter : include_router
    FastAPIApp *-- SubscriptionsRouter : include_router
    FastAPIApp ..> KafkaProducer : lifespan close()
    EventsRouter --> KafkaProducer : 2 publish after persist
    EventsRouter ..> IngestEventRequest : reads
    EventsRouter ..> IngestEventResponse : 202 body
    EndpointsRouter ..> CreateEndpointRequest : reads
    SubscriptionsRouter ..> CreateSubscriptionRequest : reads
```

---

## Implementation status

| Layer | Status |
|---|---|
| Domain entities + `CorrelationID` / `CausationID` | Implemented, `frozen=True` |
| `events_table` / `endpoints_table` / `subscriptions_table` + Alembic | Implemented |
| `DatabaseConfig` + `PersistenceEngine.get_engine()` | Implemented |
| `EventRepository` | Implemented — append-only `save()` (`ON CONFLICT DO NOTHING`) + `get_by_id()` |
| `EndpointRepository` / `SubscriptionRepository` | Implemented — upsert `save()` + `get_by_id()` |
| Kafka wrapper `publish()` / `close()` | Implemented in `relay/infrastructure/messaging/producer.py` |
| FastAPI app + routers | Implemented in `relay/api/` (`main`, `events`, `endpoints`, `subscriptions`) |
| Request DTOs | Implemented in `relay/api/schemas.py` |
| `Delivery` / `DeliveryAttempt` persistence | Not in Week 1–2 ingestion path |

## Relationship rules

- Value objects are **composition** on `Event` (nested Pydantic objects, not FK strings).
- `Subscription` / `Delivery` / `DeliveryAttempt` hold **ID associations**, not nested entities.
- API request bodies are **DTOs**, not domain entities. `IngestEventRequest` is only `event_type` + `payload`; the router builds `CorrelationID` / `CausationID` / `Event`.
- Ingestion is **persist-first**: `EventsRouter` → `EventRepository.save` → `KafkaProducer.publish` (`topic=events.business`, `key=event_id`) → HTTP 202. Kafka is not called unless Postgres committed. 202 is not returned if Kafka then fails (503, `event_id` in the body). No HTTP/Kafka retry in the route (ADR 0005).
- Routers never import `aiokafka`; they call `publish()` / `close()` on the wrapper only.
- `EventRepository` has no `update()` — append-only, matching ADR 0011.
- `EndpointRepository` / `SubscriptionRepository` `save()` **upserts**. Config entities change via `model_copy(update=...)` + re-save. `subscriptions.endpoint_id` is an FK to `endpoints`.
- `SubscriptionsRouter` resolves `endpoint_id` through `EndpointRepository` before insert (404 if missing).
- `Delivery` / `DeliveryAttempt` are kernel entities for later Data Plane work (dispatcher/workers). They are not persisted in the Week 1–2 ingestion path.
