# Relay — Domain Model

Frozen Pydantic types (ADR 0010). IDs are associations, not nested objects, except `Event` composing `CorrelationID` / `CausationID`. One `Delivery` per `(event_id, subscription_id)`. Each retry is a new `DeliveryAttempt`. `Telemetry` is 1:1 with an attempt (`attempt_id` PK). `DeliveryPolicy` is the Control Plane contract (ADR 0005), produced by AEDE and consumed, not interpreted, by the Data Plane.

Types live in `relay/kernel/` unless a section says otherwise.

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

Fan-out lifecycle.

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

    Delivery "1" --> "*" DeliveryAttempt : retries are new rows
    DeliveryAttempt "1" --> "1" Telemetry : attempt_id
```

`Delivery.status`: `PENDING` | `SUCCESS` | `FAILED` | `DEAD_LETTER`. Unique `(event_id, subscription_id)`. `deliveries` is a mutable lifecycle aggregate; attempts and telemetry are immutable facts (ADR 0014, 0019).

A **parked** delivery is `PENDING` with no attempt row, left behind when the isolation action skipped HTTP. Together with `FAILED` and `DEAD_LETTER` it forms the DLQ that replay works from (ADR 0021). Replay never edits the original rows.

---

## Policy

The contract the Control Plane hands to the Data Plane. Workers execute it mechanically.

```mermaid
classDiagram
    direction TB

    class DeliveryPolicy {
        <<frozen contract>>
        +policy_id: str
        +version: int
        +endpoint_id: str
        +endpoint_state: str
        +eri_score: float
        +computed_at: datetime
        +retry_policy: RetryPolicy
        +concurrency_policy: ConcurrencyPolicy
        +isolation_policy: IsolationPolicy
        +replay_policy: ReplayPolicy
        +notification_policy: NotificationPolicy
        +integrity_policy: IntegrityPolicy
    }

    class RetryPolicy {
        <<frozen>>
        +initial_delay_seconds: float
        +max_delay_seconds: float
        +max_retries: int
        +backoff_strategy: BackoffStrategy
        +backoff_multiplier: float
        +deadline_seconds: float
        +jitter_ratio: float
    }

    class ConcurrencyPolicy {
        <<frozen>>
        +max_parallel: int
        +queue_priority: int
        +throughput_limit: int
    }

    class IsolationPolicy {
        <<frozen>>
        +action: IsolationAction
    }

    class ReplayPolicy {
        <<frozen>>
        +allowed: bool
        +batch_size: int
        +speed: float
    }

    class NotificationPolicy {
        <<frozen>>
        +notify_admin: bool
        +alert_level: AlertLevel
        +suppress: bool
    }

    class IntegrityPolicy {
        <<frozen>>
        +hmac_required: bool
        +signature_ttl_seconds: int
        +replay_protection: bool
    }

    DeliveryPolicy *-- RetryPolicy
    DeliveryPolicy *-- ConcurrencyPolicy
    DeliveryPolicy *-- IsolationPolicy
    DeliveryPolicy *-- ReplayPolicy
    DeliveryPolicy *-- NotificationPolicy
    DeliveryPolicy *-- IntegrityPolicy
    DeliveryPolicy ..> DeliveryAttempt : governs attempt
```

`BackoffStrategy`: `FIXED` | `LINEAR` | `EXPONENTIAL`. The static baseline (Strategy A) is EXPONENTIAL, 5 s initial, ×2, capped at 300 s, 10 retries.

`IsolationAction`: `CONTINUE` | `REDUCE_CONCURRENCY` | `PAUSE` | `QUARANTINE`. `PAUSE` and `QUARANTINE` skip HTTP; the Delivery stays `PENDING` (parked).

`IntegrityPolicy.hmac_required` is set from `RELAY_HMAC_REQUIRED`, read once at process start and passed to the synthesizer as an explicit parameter, so synthesis stays deterministic. When true, the worker signs with `X-Relay-Signature`, `X-Relay-Timestamp` and `X-Relay-Nonce`.

`ReplayPolicy.allowed` governs automatic replay. Operator replay is gated by the isolation action only (ADR 0021).

---

## Control Plane records

What AEDE computes from telemetry, and the audit trail it leaves.

```mermaid
classDiagram
    direction TB

    class ERIScore {
        <<frozen VO>>
        +score: float
        +success_rate_score: float
        +latency_score: float
        +consecutive_failure_score: float
        +timeout_score: float
        +http_5xx_score: float
        +queue_depth_score: float
        +recovery_trend_score: float
        +window_size: int
        +computed_at: datetime
    }

    class ERIWeights {
        <<frozen VO>>
        +success_rate_weight: float
        +latency_weight: float
        +consecutive_failures_weight: float
        +timeout_weight: float
        +http_5xx_weight: float
        +queue_depth_weight: float
        +recovery_trend_weight: float
    }

    class EndpointState {
        <<enumeration>>
        AVAILABLE
        OBSERVED_DEGRADATION
        UNSTABLE
        QUARANTINED
        RECOVERING
    }

    class StateMachineThresholds {
        <<frozen VO>>
        +degradation_threshold: float
        +degradation_recovery_threshold: float
        +unstable_threshold: float
        +unstable_recovery_threshold: float
        +quarantine_threshold: float
        +recovery_threshold: float
    }

    class PolicyEvaluation {
        <<frozen>>
        +evaluation_id: str
        +endpoint_id: str
        +policy_id: str
        +explainability_record_id: str
        +eri_score: float
        +state_before: str
        +state_after: str
        +state_changed: bool
        +strategy_used: str
        +is_fallback: bool
        +fallback_tier: int
        +evaluated_at: datetime
        +window_size: int
    }

    class ExplainabilityRecord {
        <<frozen, control_plane>>
        +record_id: str
        +policy_id: str
        +endpoint_id: str
        +state_before: str
        +state_after: str
        +metrics: MetricSnapshot
        +thresholds: ThresholdSnapshot
        +reason: str
        +triggered_thresholds: list~str~
        +policy_changes: list~str~
        +strategy_used: str
    }

    Telemetry "*" --> "1" ERIScore : sliding window input
    ERIWeights ..> ERIScore : weights the sub-scores
    ERIScore --> EndpointState : drives transitions
    StateMachineThresholds ..> EndpointState : hysteresis bounds
    EndpointState --> DeliveryPolicy : synthesized into
    PolicyEvaluation --> DeliveryPolicy : policy_id
    PolicyEvaluation --> ExplainabilityRecord : explainability_record_id
```

ERI is a 0–100 composite of seven normalised sub-scores. Its window is attempt-count only (ADR 0009). AEDE takes `computed_at` as a parameter instead of reading the clock, so evaluation is deterministic and testable.

`ExplainabilityRecord` is not a kernel type: it lives in `relay/control_plane/explainability/record.py`. Records are held in memory today and are not persisted to Postgres.

`PolicyEvaluation.fallback_tier`: 1 = live AEDE, 2 = cached policy, 3 = static Strategy A defaults. `strategy_used`: `STRATEGY_A`, `STRATEGY_B` or `STRATEGY_C`.

---

## Transport

How the worker sends, without knowing the transport.

```mermaid
classDiagram
    direction LR

    class DeliveryAdapter {
        <<protocol>>
        +deliver(target_url, body, headers) DeliveryResponse
        +aclose()
    }

    class DeliveryResponse {
        <<frozen>>
        +status_code: int
    }

    class WebhookDeliveryAdapter {
        <<relay/adapters/webhook>>
    }

    class DeliveryTimeout {
        <<exception>>
    }

    class DeliveryTransportError {
        <<exception>>
    }

    DeliveryAdapter <|.. WebhookDeliveryAdapter
    DeliveryAdapter ..> DeliveryResponse : returns
    DeliveryAdapter ..> DeliveryTimeout : raises
    DeliveryAdapter ..> DeliveryTransportError : raises
```

The protocol sits in `relay/kernel/interfaces/` with no HTTP library behind it; only `WebhookDeliveryAdapter` depends on `httpx`. An adapter performs exactly one send. Signing, target checks and interpreting the response belong to the worker, the SSRF guard and the retry manager.

---

## Internal events

Typed internal event models (`relay/kernel/events/`). These are domain types only: the pipeline does not yet publish them on Kafka.

```mermaid
classDiagram
    direction TB

    class InternalEvent {
        <<frozen, base>>
        +correlation_id: CorrelationID
        +causation_id: CausationID
        +occurred_at: datetime
    }

    class DeliveryAttemptStarted {
        +delivery_id: str
        +attempt_id: str
        +attempt_number: int
        +endpoint_id: str
        +subscription_id: str
    }

    class DeliveryAttemptCompleted {
        +delivery_id: str
        +attempt_id: str
        +endpoint_id: str
        +http_status: int
        +success: bool
        +latency_ms: float
        +timed_out: bool
        +error_message: str
    }

    class DeliveryFailed {
        +delivery_id: str
        +endpoint_id: str
        +business_event_id: str
        +attempt_count: int
        +status: FAILED or DEAD_LETTER
    }

    class DeadLetterRouted {
        +delivery_id: str
        +endpoint_id: str
        +business_event_id: str
        +reason: FAILED, DEAD_LETTER or PARKED
    }

    class AlertTriggered {
        +endpoint_id: str
        +state: str
        +eri: float
        +reason: str
    }

    InternalEvent <|-- DeliveryAttemptStarted
    InternalEvent <|-- DeliveryAttemptCompleted
    InternalEvent <|-- DeliveryFailed
    InternalEvent <|-- DeadLetterRouted
    InternalEvent <|-- AlertTriggered
```

Policy, endpoint and replay event types are added separately and will subclass `InternalEvent` as well.

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
    PolicyEvaluation "*" --> "1" Endpoint : endpoint_id
    PolicyEvaluation "1" --> "1" DeliveryPolicy : policy_id
```
