# Chapter 3 — System Design

## 3.1 Architectural Overview

Relay is an Event Delivery Platform that treats outbound webhook delivery as a distributed systems problem rather than an HTTP problem. Instead of wrapping delivery in application-level retry loops, Relay centres the architecture around immutable events flowing through a durable pipeline. Applications publish business events; the platform owns the entire downstream lifecycle — scheduling, delivery, retry, replay, failure isolation, monitoring, and observability.

Seven guiding principles inform every architectural decision:

1. **Events are Immutable** — Events describe facts. They are never modified after publication.
2. **Delivery is Infrastructure** — Applications never contain retry loops or replay logic.
3. **Failure is Expected** — Every component assumes external systems will eventually fail.
4. **Observability is First-Class** — Every event is traceable from creation to final delivery.
5. **Developer Experience Comes First** — Complexity belongs inside the platform, not in applications.
6. **Extensibility Over Configuration** — New delivery mechanisms are implemented as adapters, not configuration flags.
7. **Intelligence Must Be Explainable** — Every automated decision exposes why it was made.

The system is organised into five layers, each with strictly defined responsibilities:

```
                   Applications
                         │
                  SDK / REST API
                         │
               ─────────────────────
                    Relay Platform
               ─────────────────────

              Layer 1: Developer Interfaces
          Dashboard  •  SDK  •  API

               ─────────────────────

              Layer 2: Control Plane
          Adaptive Event Delivery Engine (AEDE)

    ┌───────────────────────────────────────────┐
    │  Endpoint Reliability Index (ERI)         │
    │  Endpoint State Machine                   │
    │  Policy Synthesizer                       │
    │  Explainability Engine                    │
    └───────────────────────────────────────────┘

               ─────────────────────

              Layer 3: Messaging Backbone (Kafka)

               ─────────────────────

              Layer 4: Data Plane
    Dispatcher → Workers → Delivery Adapters
    Retry Manager

               ─────────────────────

              Layer 5: Infrastructure
    PostgreSQL  •  Redis  •  Docker
```

---

## 3.2 Control Plane / Data Plane Separation

The fundamental architectural law of Relay is:

> **The Control Plane decides. The Data Plane executes. This separation is inviolable.**

The **Control Plane** evaluates endpoint behaviour continuously, computes the Endpoint Reliability Index (ERI), drives the Endpoint State Machine, synthesises immutable Delivery Policies, and produces Explainability Records for every decision. It answers: *Is this endpoint healthy? Should delivery continue? Should retries be delayed? Should the endpoint be quarantined? Should administrators be notified?*

The **Data Plane** ingests events, publishes them to Kafka, dispatches them to workers, executes Delivery Policies (HTTP requests, HMAC signing, payload serialisation), and collects telemetry. It answers: *Which event should be delivered? Has delivery succeeded? Should this event move to the retry queue?*

This separation yields four concrete benefits:

| Benefit | Mechanism |
|---|---|
| **Scalability** | Workers scale independently from policy services |
| **Extensibility** | New delivery transports do not require changes to policy logic |
| **Testability** | Identical telemetry always produces identical policies; workers can be benchmarked independently |
| **Explainability** | Every action is traceable — the endpoint state and policy that produced any delivery attempt can be inspected retroactively |

The separation is enforced at the code level: policy logic (retry interval computation, state transitions, quarantine decisions) never appears in worker code. Workers receive a complete, immutable `DeliveryPolicy` and execute it without interpretation.

---

## 3.3 Adaptive Event Delivery Engine (AEDE)

AEDE is the decision-making core of the platform and the primary research contribution. It does not deliver events. It continuously evaluates endpoint operational state and synthesises delivery policies that the Data Plane consumes.

### 3.3.1 Design Philosophy

Conventional webhook delivery systems react at the request level:

```
Request → Failed → Wait → Retry
```

AEDE reasons about endpoint behaviour over time, asking a fundamentally different question:

> "What does this failure tell us about the current operational state of this endpoint?"

This shift — from request-level reaction to endpoint-level reasoning — is the core architectural insight. A single HTTP 503 response is an observation. Twenty consecutive 503 responses within a sliding window constitute evidence of sustained degradation. AEDE distinguishes between the two and adjusts delivery strategy accordingly.

### 3.3.2 Inputs

AEDE receives externally observable telemetry only. It has no access to subscriber infrastructure, no sidecar proxies, no distributed traces. The observable signal set is limited to:

- HTTP response codes and response latency
- Connection timeouts, TLS errors, DNS failures
- Consecutive failure and success streaks
- Retry history and queue depth

This constraint — deriving endpoint health from external observations alone — distinguishes AEDE from adaptive controllers in service meshes that assume control over both communicating services (see Section 4.2, Constraints 1–4).

### 3.3.3 Output: Delivery Policy

AEDE synthesises an immutable `DeliveryPolicy` object containing six sub-policies:

```
DeliveryPolicy
├── RetryPolicy          (initial_delay, max_delay, max_retries, backoff_strategy)
├── ConcurrencyPolicy    (max_parallel, queue_priority)
├── IsolationPolicy      (continue | reduce_concurrency | pause | quarantine)
├── ReplayPolicy         (allowed, batch_size, speed)
├── NotificationPolicy   (notify_admin, alert_level)
└── IntegrityPolicy      (hmac_required)
```

The `DeliveryPolicy` is the contract between the Control Plane and the Data Plane. Workers consume this contract and execute it without interpretation. The policy object is immutable — once produced for a delivery attempt, it is never modified.

### 3.3.4 Determinism Requirement

Given identical endpoint observations, identical configuration, and identical policy rules, AEDE must always generate identical Delivery Policies. This is a fundamental design requirement (ADR 0004), not a guideline.

Determinism is enforced through two mechanisms:

1. **No internal wall-clock dependency.** AEDE never calls `datetime.now()` or `time.time()` for any policy-relevant computation. Any time boundary required is passed as an explicit parameter by the caller.
2. **No randomness in policy evaluation.** The ERI computation, state machine transitions, and policy synthesis are pure functions of their inputs.

This determinism guarantee enables experimental reproducibility: the same telemetry sequence produces the same policy decisions across runs, making Strategy A vs B vs C comparison meaningful.

---

## 3.4 Endpoint Reliability Index (ERI)

The ERI is a deterministic composite score on a 0–100 scale representing observed endpoint reliability. A score of 100 indicates a perfectly healthy endpoint; a score of 0 indicates an endpoint considered completely unavailable. The ERI is the sole input to state machine transitions.

### 3.4.1 Composite Score Computation

The ERI is computed as a weighted sum of seven normalised sub-metrics, each derived from externally observable signals:

| Sub-Metric | Default Weight | Signal Source | Normalisation |
|---|---:|---|---|
| Success Rate | 35% | Fraction of successful delivery attempts in the window | `successes / total_attempts` |
| Average Response Latency | 20% | Mean response time of non-timed-out attempts | Linear decay from `baseline_latency` to `max_latency` |
| Consecutive Failures | 20% | Length of the trailing failure streak | `1.0 - (streak / max_consecutive_failures)` |
| Timeout Rate | 10% | Fraction of attempts that timed out | `(total - timeouts) / total` |
| HTTP 5xx Rate | 5% | Fraction of attempts returning 5xx status codes | `(total - 5xx_count) / total` |
| Queue Depth | 5% | Current retry queue depth for the endpoint | `1.0 - (depth / max_queue_depth)` |
| Recovery Trend | 5% | Whether the second half of the window performs better than the first | Binary comparison of half-window success rates |

The composite score is:

$$
\text{ERI} = \left( \sum_{i=1}^{7} w_i \cdot s_i \right) \times 100
$$

where $w_i$ is the weight and $s_i$ is the normalised score (0.0–1.0) for sub-metric $i$, and $\sum w_i = 1.0$ (validated at construction time).

The weights are disclosed design parameters — a reasonable starting configuration. They are not claimed to be optimal. The weighting model itself is not the research contribution; the contribution is how the resulting score drives runtime policy decisions through the state machine and policy synthesiser.

### 3.4.2 Sliding Observation Window

The ERI is computed over the last $N$ delivery attempts (default: $N = 100$). No time-based filtering is applied. This is a deliberate design choice (ADR 0009) to preserve strict determinism: a time-based window (e.g., "last 5 minutes") means two evaluations at slightly different wall-clock offsets include different telemetry records and produce different ERI scores from identical history, breaking reproducibility for benchmark experiments. An attempt-count window is a pure function of the telemetry set.

The tradeoff is acknowledged: a count-based window responds differently for high-volume versus low-volume endpoints. An endpoint with 100 attempts in the last 30 seconds gets a very recent window; one with 100 attempts over the last week gets a stale one. This is accepted for the MVP. A time-bounded secondary filter (with the cutoff passed as an explicit parameter, never read from wall-clock) is noted as a post-MVP enhancement.

### 3.4.3 Sub-Metric Preservation

Every ERI evaluation preserves all seven individual sub-scores in a `MetricSnapshot` embedded within the `ExplainabilityRecord`. This enables post-hoc analysis of whether the composite score accurately reflected the endpoint's actual condition during each experiment — directly addressing the construct validity threat identified in Section 4.6.

---

## 3.5 Endpoint State Machine

Each endpoint exists in exactly one of five operational states at all times. State transitions are driven exclusively by the ERI score.

### 3.5.1 State Definitions

| State | Operational Meaning | Policy Profile |
|---|---|---|
| **AVAILABLE** | Operating normally. High success rate, low latency, stable behaviour. | Immediate delivery, maximum concurrency, standard monitoring. |
| **OBSERVED_DEGRADATION** | Early warning. Increasing latency, isolated failures, elevated timeout frequency. | Moderate retry backoff, reduced parallel delivery (max 3), increased monitoring. |
| **UNSTABLE** | Persistent instability. Repeated failures, poor success rate, sustained latency increase. | Aggressive backoff, single-stream delivery (max 1), pause new deliveries, notify administrator. |
| **QUARANTINED** | Delivery intentionally suspended. Endpoint considered unavailable. | Zero delivery, preserve all queued events, await recovery evidence. |
| **RECOVERING** | Endpoint has shown evidence of recovery but stability is not yet confirmed. | Gradual traffic restoration (single-stream), conservative retry intervals, intensive monitoring. |

### 3.5.2 Hysteresis

Simple threshold-based transitions produce oscillation: an endpoint hovering near a boundary alternates rapidly between states, creating unstable policy behaviour. Hysteresis prevents this by requiring different thresholds for degradation and recovery:

| Transition Direction | Threshold |
|---|---|
| Degrade into OBSERVED_DEGRADATION | ERI < 80 |
| Recover from OBSERVED_DEGRADATION | ERI > 90 |
| Degrade into UNSTABLE | ERI < 60 |
| Recover from UNSTABLE | ERI > 75 |
| Degrade into QUARANTINED | ERI < 30 |
| Recover to AVAILABLE from RECOVERING | ERI > 90 |

The asymmetry — recovery thresholds are strictly higher than degradation thresholds — implements the design principle: *trust failure quickly; trust recovery cautiously*. This is validated by a Pydantic model validator (`StateMachineThresholds`) that enforces `degradation_recovery_threshold > degradation_threshold` and `unstable_recovery_threshold > unstable_threshold` at construction time.

### 3.5.3 Non-Adjacent Transitions

The canonical state progression is linear (AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED → RECOVERING → AVAILABLE), but real endpoint behaviour is not monotonic. The state machine defines explicit rules for non-adjacent transitions:

| From | To | Trigger |
|---|---|---|
| AVAILABLE | QUARANTINED | ERI collapses below quarantine threshold in a single window |
| OBSERVED_DEGRADATION | QUARANTINED | Sudden complete failure bypasses UNSTABLE |
| RECOVERING | QUARANTINED | ERI collapses again during recovery — re-quarantine |
| Any state | QUARANTINED | Manual administrative override |

The two most operationally important non-adjacent transitions are:

- **OBSERVED_DEGRADATION → QUARANTINED**: Handles sudden complete outages (e.g., 20 consecutive HTTP 503 responses) without waiting for UNSTABLE accumulation.
- **RECOVERING → QUARANTINED**: Prevents premature recovery from becoming a cycle. An endpoint that fails again during gradual traffic restoration re-enters quarantine immediately.

### 3.5.4 Quarantine as a Terminal State

QUARANTINED is a terminal state in the state machine — the `next_state()` function always returns QUARANTINED when the current state is QUARANTINED, regardless of ERI improvement (ADR 0012). Exit from quarantine requires an external trigger: a scheduled health probe or a manual administrative action that transitions the endpoint to RECOVERING. This prevents an endpoint from silently recovering while accumulated events are lost.

---

## 3.6 Policy Synthesiser

The Policy Synthesiser is the pure function at the core of AEDE. It maps endpoint state and ERI into an immutable `DeliveryPolicy` and a corresponding `ExplainabilityRecord`:

```python
def synthesize_policy(
    endpoint_id: str,
    state_before: EndpointState,
    state_after: EndpointState,
    eri: ERIScore,
    thresholds: StateMachineThresholds,
    computed_at: datetime,
) -> Tuple[DeliveryPolicy, ExplainabilityRecord]
```

The mapping from state to policy profile is:

| State | Retry | Concurrency | Isolation | Admin Alert |
|---|---|---|---|---|
| AVAILABLE | 3s initial, max 10 retries | max_parallel = 10 | CONTINUE | No |
| OBSERVED_DEGRADATION | 5s initial, max 8 retries | max_parallel = 3 | REDUCE_CONCURRENCY | No |
| UNSTABLE | 15s initial, max 5 retries | max_parallel = 1 | PAUSE | Yes (WARNING) |
| QUARANTINED | No delivery (max_retries = 0) | max_parallel = 1 | QUARANTINE | Yes (CRITICAL) |
| RECOVERING | 10s initial, max 6 retries | max_parallel = 1 | CONTINUE | No |

The synthesiser generates a human-readable `reason` string, a list of `triggered_thresholds`, and a list of `policy_changes` for every evaluation — regardless of whether a state transition occurred. When no transition occurs, the record documents that the existing policy profile was maintained and why.

---

## 3.7 Three-Tier Policy Fallback

AEDE is not a single point of failure. When AEDE is unavailable, the platform degrades gracefully through a three-tier fallback hierarchy (ADR 0011):

```
Tier 1: AEDE live policy          (normal operation)
         ↓ if AEDE unreachable
Tier 2: Redis-cached policy       (last known good, per endpoint, TTL: 5 minutes)
         ↓ if cache cold or expired
Tier 3: Static fallback policy    (safe defaults — Strategy A equivalent)
```

**Tier 2** uses a Redis cache with a 5-minute TTL (configurable via `POLICY_CACHE_TTL_SECONDS`). The TTL is deliberately short: it prevents a stale "AVAILABLE — deliver immediately" policy cached before an AEDE outage from being served indefinitely as an endpoint degrades.

**Tier 3** is a conservative, pre-configured policy: exponential backoff with `max_parallel = 1`, `max_retries = 10`, `notify_admin = true`. This is functionally equivalent to Strategy A in the research evaluation. Delivery continues; adaptive intelligence is suspended. When AEDE recovers, it re-evaluates all active endpoints and resumes normal policy synthesis.

An AEDE outage is therefore an operational degradation event, not a platform outage.

---

## 3.8 Explainability Engine

Every policy evaluation produces an immutable `ExplainabilityRecord` (ADR 0013). This record answers the question: *Why did AEDE make this decision?*

An ExplainabilityRecord contains:

| Field | Purpose |
|---|---|
| `record_id` | Globally unique identifier |
| `policy_id` | Links to the `DeliveryPolicy` this record explains |
| `endpoint_id` | The evaluated endpoint |
| `state_before` / `state_after` | Endpoint state transition (or confirmation of no change) |
| `metrics` | `MetricSnapshot` — all seven ERI sub-scores at decision time |
| `thresholds` | `ThresholdSnapshot` — state machine thresholds in effect |
| `reason` | Human-readable explanation (e.g., "ERI fell to 72.0, transitioning to OBSERVED_DEGRADATION") |
| `triggered_thresholds` | Which thresholds were crossed (e.g., "ERI 72.0 < degradation_threshold 80.0") |
| `policy_changes` | What policy fields changed (e.g., "max_parallel: 10 → 3") |
| `strategy_used` | STRATEGY_A, STRATEGY_B, or STRATEGY_C |
| `evaluated_at` | Caller-supplied timestamp (never `datetime.now()`) |
| `window_size` | Number of telemetry records in the observation window |

The preservation of both `MetricSnapshot` and `ThresholdSnapshot` enables retrospective analysis: for any historical policy decision, the exact sub-metric values and threshold configuration that produced it can be retrieved without recomputing ERI from raw telemetry.

ExplainabilityRecords are the primary research artifact for validating 100% explainability coverage — the target that every automated decision has a complete, human-readable explanation.

---

## 3.9 Feature Flags for Research

Feature flags enable switching delivery strategies without redeployment. This is the mechanism for benchmarking Strategy A vs B vs C:

| Flag | Effect |
|---|---|
| `AEDE_ENABLED` | Full adaptive policy engine vs fallback |
| `ERI_ENABLED` | ERI-driven state machine vs static health |
| `STATE_MACHINE_ENABLED` | Full 5-state model vs binary healthy/unhealthy |
| `STATIC_RETRY_MODE` | Fixed retry interval (Strategy A baseline) |
| `ADAPTIVE_RETRY_MODE` | Adaptive backoff without state model (Strategy B baseline) |

The benchmark flow is: configure flags → run benchmark suite → collect metrics → compare. All three strategies produce `DeliveryPolicy` objects through the same `DeliveryStrategy` interface, ensuring that the only variable between experiments is the policy logic itself.

`STATIC_RETRY_MODE` serves a dual purpose: it is both a research baseline flag and the automatic fallback when the policy hierarchy reaches Tier 3. This means Strategy A is not merely a research artefact — it is the system's documented safe degradation behaviour.

---

## 3.10 Domain Model

### 3.10.1 Primary Entities

| Entity | Responsibility |
|---|---|
| **Event** | Immutable business fact (e.g., `OrderCreated`). Never modified after publication. |
| **Endpoint** | A delivery destination (HTTP webhook URL in v1). Owns configuration only, not runtime state. |
| **EndpointState** | Operational classification derived from ERI. Computed, never manually assigned. |
| **Delivery** | The platform's responsibility to deliver one Event to one Endpoint. |
| **DeliveryAttempt** | A single execution against an Endpoint. Immutable. Each retry is a new attempt. |
| **DeliveryPolicy** | Immutable operational contract from AEDE. Consumed by workers. |
| **PolicyEvaluation** | One AEDE execution. Links the ERI, state transition, and produced policy. |
| **Telemetry** | Observations from DeliveryAttempts (HTTP status, latency, timeout). |
| **ExplainabilityRecord** | Immutable record of metrics, thresholds, and reasoning for every policy decision. |

### 3.10.2 Domain Relationships

```
Application
  └─publishes→ Event
                 └─creates→ Delivery (one per matched Subscription)
                              └─contains→ DeliveryAttempts
                                            └─execute→ DeliveryPolicy
                                                          └─produced by→ PolicyEvaluation
                                                                           └─computed from→ EndpointState
                                                                                              └─derived from→ ERI
                                                                                                               └─computed from→ Telemetry
```

Every entity in this chain is immutable after creation. Corrections or updates are new records, never mutations of existing records. This immutability guarantee enables complete delivery timeline reconstruction and audit.

---

## 3.11 Event Lifecycle

A business event passes through eleven stages from publication to final delivery:

1. **Event Publication** — Application publishes via SDK or REST API.
2. **Event Ingestion** — Authentication, validation, metadata enrichment, event ID generation, timestamp assignment.
3. **Durable Event Storage** — Validated events are appended to Kafka. The platform never delivers transient in-memory events.
4. **Dispatch** — The Dispatcher consumes events from Kafka, identifies the destination endpoint, requests the current Delivery Policy from AEDE, and assigns work to a Worker.
5. **Policy Evaluation** — Before every DeliveryAttempt, the Dispatcher requests the latest Delivery Policy from AEDE.
6. **Delivery** — Workers execute the policy: HMAC generation, HTTP request construction, payload serialisation, response collection.
7. **Telemetry Collection** — Workers publish telemetry (HTTP status, latency, timeout). Workers do not interpret telemetry.
8. **Endpoint Evaluation** — AEDE consumes telemetry, recomputes the ERI, and if warranted triggers state transitions and new policies.
9. **Failure Handling** — Failed deliveries are routed through the Retry Manager. Each retry is a new DeliveryAttempt with a fresh policy evaluation.
10. **Quarantine** — When AEDE determines QUARANTINED state: deliveries stop, events remain durable, administrators are notified.
11. **Recovery** — When evidence indicates recovery: gradual replay begins, successful delivery transitions the endpoint back to AVAILABLE.

### Lifecycle Guarantees

- Every Event receives a globally unique identifier.
- Every DeliveryAttempt receives its own identifier.
- Every PolicyEvaluation receives its own identifier.
- Every state transition is recorded.
- Every automated decision is explainable.
- Every failed delivery remains recoverable.
- No event disappears silently.

---

## 3.12 Technology Stack

| Layer | Technology | Justification |
|---|---|---|
| API | Python 3.11+, FastAPI | Async-capable, Pydantic-native validation, OpenAPI generation (ADR 0006) |
| Domain Models | Pydantic v2 | Frozen models enforce immutability at the type level (ADR 0010) |
| Messaging Backbone | Apache Kafka | Durable log model, replay capability, partitioned consumption (ADR 0001) |
| Primary Database | PostgreSQL | ACID guarantees, JSON support, mature ecosystem (ADR 0007) |
| Policy Cache | Redis | Sub-millisecond policy lookups, TTL-based expiry |
| Containerisation | Docker, Docker Compose | Reproducible development environment |

---

## 3.13 Configuration Philosophy

No thresholds are hardcoded. All operational parameters are externalised in configuration:

```yaml
eri:
  success_rate_weight: 0.35
  latency_weight: 0.20
  consecutive_failures_weight: 0.20
  timeout_weight: 0.10
  http_5xx_weight: 0.05
  queue_depth_weight: 0.05
  recovery_trend_weight: 0.05

state_machine:
  degradation_threshold: 80
  unstable_threshold: 60
  quarantine_threshold: 30
  recovery_threshold: 90

adaptive_policy:
  quarantine_duration_seconds: 300
  replay_batch_size: 50
  recovery_parallelism: 1
```

This externalisation enables the weight sensitivity analysis described in Section 4.5 — alternative ERI weight configurations can be evaluated without code changes.
