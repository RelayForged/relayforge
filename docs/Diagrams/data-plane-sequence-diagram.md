# Data Plane — End-to-End Sequences

Persist-first (ADR 0001, 0002): Postgres before Kafka on ingest, `Delivery` and attempt before HTTP on dispatch, Kafka offset after Postgres and never after HTTP. The Worker does not compute backoff or choose a strategy: the policy it receives already says what to do (ADR 0005). Retry Manager applies `retry_policy` fields and publishes `events.internal.delivery`.

Four flows share these participants: [ingest and delivery](#1-ingest-and-first-delivery), [retry](#2-retry), [replay from the DLQ](#3-replay-from-the-dlq) and [operator restore](#4-operator-restore).

---

## 1. Ingest and first delivery

```mermaid
sequenceDiagram
    autonumber
    actor Pub as Publisher
    participant API as FastAPI
    participant PG as Postgres
    participant K as Kafka
    participant D as Dispatcher
    participant AEDE as AEDEService
    participant W as WebhookWorker
    participant A as DeliveryAdapter
    participant Sub as Subscriber Webhook
    participant R as RetryManager
    participant Dash as Dashboard backend

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
    D->>PG: list active subscriptions by event_type
    PG-->>D: (Subscription, Endpoint)[]

    loop each ACTIVE subscription
        D->>PG: INSERT deliveries PENDING<br/>unique (event_id, subscription_id)
        PG-->>D: Delivery

        Note over D,AEDE: Policy, three-tier fallback (ADR 0011)
        alt Tier 1 - AEDE live
            D->>AEDE: get_current_policy(endpoint_id, computed_at)
            AEDE-->>D: DeliveryPolicy (state, ERI, sub-policies)
            D->>D: cache policy, TTL 300 s
        else Tier 2 - cached policy within TTL
            D->>D: InMemoryPolicyCache.get_policy
        else Tier 3 - static Strategy A defaults
            D->>D: PolicyFallbackManager static policy
        end

        alt isolation PAUSE or QUARANTINE
            D->>D: skip HTTP, no attempt row<br/>Delivery stays PENDING (parked)
        else CONTINUE or REDUCE_CONCURRENCY
            D->>PG: INSERT delivery_attempts attempt_number=1
            PG-->>D: DeliveryAttempt
            D->>W: execute(attempt, target_url, policy)
            activate W
            W->>W: SSRF guard, build headers<br/>X-Relay-Event-Id, X-Relay-Attempt-Id
            opt policy.integrity_policy.hmac_required
                W->>W: add X-Relay-Signature,<br/>X-Relay-Timestamp, X-Relay-Nonce
            end
            W->>A: deliver(target_url, body, headers)
            A->>Sub: HTTP POST
            Sub-->>A: status or timeout
            A-->>W: DeliveryResponse or DeliveryTimeout
            W->>PG: complete DeliveryAttempt
            W->>PG: INSERT telemetry
            W->>AEDE: record_telemetry(Telemetry)
            activate AEDE
            AEDE->>AEDE: ERI over sliding window,<br/>state machine, synthesize policy
            AEDE->>K: publish events.internal.policy<br/>key=endpoint_id
            deactivate AEDE
            K->>Dash: consume events.internal.policy
            Dash->>Dash: update state_store, push over /ws
            W->>R: handle(completed, delivery, policy)
            activate R
            alt success
                R->>PG: Delivery SUCCESS
            else retries exhausted
                R->>PG: Delivery FAILED
            else retryable failure
                R->>PG: INSERT next DeliveryAttempt
                R->>K: publish events.internal.delivery<br/>key=event_id RetryWork
                K-->>R: ack
            end
            deactivate R
            deactivate W
        end
    end

    D->>K: commit offset
    deactivate D
```

**Policy source.** With `AEDE_ENABLED=true` the dispatcher's `run()` builds an `AEDEService`, so Tier 1 is always the live AEDE policy. `STATIC_RETRY_MODE` and `ADAPTIVE_RETRY_MODE` only pick the strategy object (Strategy A or B) used when a dispatcher has no `AEDEService`; they do not change the policy source of the shipped process. With `AEDE_ENABLED=false` there is no Tier 1 at all and the policy comes from the cache, then the static Strategy A defaults. A Tier 1 failure falls through to Tier 2, then Tier 3.

**Parked deliveries.** A `PENDING` delivery with no attempt row, left behind by `PAUSE` or `QUARANTINE`, is what the DLQ calls `PARKED` (ADR 0021). Nothing re-dispatches it automatically; operator replay does.

**Metrics.** The dispatcher is the process that records attempts, ERI and state. Its Prometheus exporter listens on `RELAY_METRICS_PORT` (default 9100) once `start_metrics_server()` is called at startup. That call is not wired yet, so the dispatcher currently exports nothing and the Prometheus `dispatcher` target is down.

---

## 2. Retry

```mermaid
sequenceDiagram
    autonumber
    participant K as Kafka
    participant D as Dispatcher
    participant PG as Postgres
    participant W as WebhookWorker

    K->>D: consume events.internal.delivery (RetryWork)
    activate D
    D->>D: wait until next_attempt_at<br/>(delay = min(initial x multiplier^(n-1), max))
    D->>W: execute(next attempt, policy)
    W->>PG: complete attempt, INSERT telemetry
    Note over D,W: Same worker path as the first attempt:<br/>telemetry to AEDE, then RetryManager.handle
    D->>K: commit offset after the attempt is persisted
    deactivate D
```

The wait lives in the Dispatcher only; the Worker and the Retry Manager never sleep. A crash during the wait leaves the offset uncommitted, so the retry is redelivered instead of dropped.

---

## 3. Replay from the DLQ

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant API as FastAPI
    participant RM as ReplayManager
    participant PG as Postgres
    participant K as Kafka
    participant D as Dispatcher
    participant W as WebhookWorker

    Op->>API: POST /replay {endpoint_id, delivery_ids?, limit?}
    API->>RM: replay_dead_letters
    RM->>PG: select FAILED, DEAD_LETTER and PARKED deliveries,<br/>excluding ones already replayed
    PG-->>RM: dead letters
    loop each delivery
        RM->>K: publish events.internal.replay (ReplayWork)
    end
    API-->>Op: 202 {replay_id, requested, skipped}

    K->>D: consume events.internal.replay
    activate D
    alt PARKED delivery
        D->>D: dispatch the original Event again,<br/>Delivery row already exists
    else FAILED or DEAD_LETTER
        D->>D: dispatch a new Event, same type and payload,<br/>causation_id = original event_id
    end
    D->>PG: persist Delivery (reused when PARKED, new for a new Event)<br/>and attempt 1
    Note over D,W: Normal _dispatch_one path: policy from the Control Plane.<br/>A quarantined endpoint is parked again, not contacted.
    D->>W: execute(attempt, policy)
    D->>K: commit offset after the work is persisted
    deactivate D
```

Original delivery, attempt and telemetry rows are never modified (ADR 0021). A repeat replay of the same delivery is refused.

---

## 4. Operator restore

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant API as FastAPI
    participant K as Kafka
    participant D as Dispatcher
    participant AEDE as AEDEService
    participant Dash as Dashboard backend

    Op->>API: POST /endpoints/{id}/restore
    API->>K: publish events.internal.endpoint<br/>{action: restore, endpoint_id}
    API-->>Op: 200
    K->>D: consume events.internal.endpoint
    D->>AEDE: restore_and_publish(endpoint_id, restored_at)
    AEDE->>AEDE: QUARANTINED to RECOVERING, replace cached policy,<br/>reset ERI window (ADR 0020)
    AEDE->>K: publish events.internal.policy
    K->>Dash: consume events.internal.policy
    D->>K: commit offset
```
