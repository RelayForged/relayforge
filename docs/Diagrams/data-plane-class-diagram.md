# Data Plane — Domain Model (Weeks 1–4)

Frozen Pydantic types (`relay/kernel/`). IDs are associations, not nested objects, except `Event` composing `CorrelationID` / `CausationID`. One `Delivery` per `(event_id, subscription_id)`. Each retry is a new `DeliveryAttempt`. `Telemetry` is 1:1 with an attempt (`attempt_id` PK). `DeliveryPolicy` is the Control Plane contract (ADR 0005); Week 3–4 produces it via `StrategyA`.

---

## Core

Configuration and ingested facts.

```mermaid
classDiagram
    direction TB

    class Event {
        <<frozen>>
        +event_id: str
        +correlation_id: CorrelationID
        +causation_id: CausationID
        +event_type: str
        +payload: dict
        +occurred_at: datetime
    }

    class CorrelationID {
        <<frozen VO>>
        +value: str
    }

    class CausationID {
        <<frozen VO>>
        +value: str
    }

    class Endpoint {
        <<frozen>>
        +endpoint_id: str
        +target_url: str
        +owner_id: str
        +created_at: datetime
        +is_active: bool
    }

    class Subscription {
        <<frozen>>
        +subscription_id: str
        +endpoint_id: str
        +event_types: list~str~
        +created_at: datetime
        +status: str
    }

    Event *-- CorrelationID
    Event *-- CausationID
    Endpoint "1" --> "*" Subscription : endpoint_id
```

---

## Execution

Fan-out lifecycle and the policy workers execute.

```mermaid
classDiagram
    direction TB

    class Delivery {
        <<frozen>>
        +delivery_id: str
        +event_id: str
        +subscription_id: str
        +endpoint_id: str
        +created_at: datetime
        +status: str
    }

    class DeliveryAttempt {
        <<frozen>>
        +attempt_id: str
        +delivery_id: str
        +attempt_number: int
        +started_at: datetime
        +completed_at: datetime
        +http_status: int
        +success: bool
        +error_message: str
    }

    class DeliveryPolicy {
        <<frozen contract>>
        +policy_id: str
        +version: int
        +endpoint_id: str
        +endpoint_state: str
        +eri_score: float
        +computed_at: datetime
        +retry_policy: RetryPolicy
        +isolation_policy: IsolationPolicy
    }

    class RetryPolicy {
        <<frozen>>
        +initial_delay_seconds: float
        +max_delay_seconds: float
        +max_retries: int
        +backoff_strategy: str
        +backoff_multiplier: float
    }

    class IsolationPolicy {
        <<frozen>>
        +action: IsolationAction
    }

    Delivery "1" --> "*" DeliveryAttempt : retries are new rows
    DeliveryPolicy *-- RetryPolicy
    DeliveryPolicy *-- IsolationPolicy
    DeliveryPolicy ..> DeliveryAttempt : governs attempt
```

`Delivery.status`: `PENDING` | `SUCCESS` | `FAILED` | `DEAD_LETTER`. Unique `(event_id, subscription_id)`.

`RetryPolicy` (Strategy A): EXPONENTIAL, `initial_delay_seconds=5`, `backoff_multiplier=2`, `max_delay_seconds=300`, `max_retries=10`.

`IsolationAction`: `CONTINUE` | `REDUCE_CONCURRENCY` | `PAUSE` | `QUARANTINE`. `PAUSE` / `QUARANTINE` skip HTTP; Delivery stays `PENDING`.

---

## Observability

ERI input. Not a `DeliveryAttempt`. PK is `attempt_id`.

```mermaid
classDiagram
    direction TB

    class Telemetry {
        <<frozen VO>>
        +attempt_id: str
        +endpoint_id: str
        +succeeded: bool
        +http_status: int
        +latency_ms: float
        +timed_out: bool
        +observed_at: datetime
        +is_http_5xx() bool
    }

    class DeliveryAttempt {
        <<frozen>>
        +attempt_id: str
        +delivery_id: str
    }

    DeliveryAttempt "1" --> "1" Telemetry : attempt_id
```

---

## Cardinality map

```mermaid
classDiagram
    direction LR

    Event "1" --> "*" Delivery : event_id
    Subscription "1" --> "*" Delivery : subscription_id
    Endpoint "1" --> "*" Delivery : endpoint_id
    Endpoint "1" --> "*" Subscription : endpoint_id
    Delivery "1" --> "*" DeliveryAttempt : delivery_id
    DeliveryAttempt "1" --> "1" Telemetry : attempt_id
    DeliveryPolicy ..> Delivery : evaluated per endpoint
```
