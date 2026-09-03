# Data Plane — End-to-End Sequence (Weeks 1–4)

Persist-first (ADR 0001, 0002): Postgres before Kafka on ingest; Delivery + attempt before HTTP on dispatch; Kafka offset after Postgres, never after HTTP. Policy is Strategy A (ADR 0005). Worker does not compute backoff. Retry Manager applies `retry_policy` fields and publishes `events.internal.delivery`.

```mermaid
sequenceDiagram
    autonumber
    actor Pub as Publisher
    participant API as FastAPI
    participant PG as Postgres
    participant K as Kafka
    participant D as Dispatcher
    participant SA as StrategyA
    participant W as WebhookWorker
    participant Sim as Simulator
    participant R as RetryManager

    Pub->>API: POST /events {event_type, payload}
    activate API
    API->>PG: INSERT events
    PG-->>API: committed
    API->>K: publish events.business<br/>key=event_id
    K-->>API: ack
    API-->>Pub: 202 {event_id}
    deactivate API

    K->>D: consume events.business
    activate D
    D->>D: Event.model_validate_json
    D->>PG: list_active_by_event_type
    PG-->>D: Subscription[]

    loop each ACTIVE Subscription
        D->>PG: INSERT deliveries PENDING<br/>(event_id, subscription_id)
        PG-->>D: Delivery
        D->>SA: evaluate(endpoint_id, AVAILABLE, eri, computed_at)
        activate SA
        SA-->>D: DeliveryPolicy
        deactivate SA

        alt isolation PAUSE or QUARANTINE
            D->>D: skip HTTP — Delivery stays PENDING
        else CONTINUE
            D->>PG: INSERT delivery_attempts attempt_number=1
            PG-->>D: DeliveryAttempt
            D->>W: execute(attempt, target_url, endpoint_id, policy)
            activate W
            W->>Sim: HTTP POST /webhook
            activate Sim
            Sim-->>W: 5xx
            deactivate Sim
            W->>PG: complete DeliveryAttempt
            W->>PG: INSERT telemetry
            PG-->>W: persisted
            W->>R: handle(completed, delivery, policy)
            activate R
            R->>R: delay = min(5 * 2^(n-1), 300)
            R->>PG: INSERT next DeliveryAttempt
            R->>K: publish events.internal.delivery<br/>key=event_id RetryWork
            K-->>R: ack
            deactivate R
            deactivate W
        end
    end

    D->>K: commit offset
    deactivate D

    K->>D: consume events.internal.delivery
    activate D
    D->>D: wait until next_attempt_at
    D->>W: execute(next attempt, ...)
    deactivate D
```
