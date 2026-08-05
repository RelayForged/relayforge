# Relay — System Architecture

## Overview

Relay is an **Event Delivery Platform**: production-grade infrastructure for reliable outbound event delivery. It treats webhook delivery as a distributed systems problem rather than an HTTP problem.

Instead of centering the system around HTTP requests, Relay centers it around immutable events flowing through a durable pipeline. Applications publish business events. The platform owns everything after that — scheduling, delivery, retry, replay, failure handling, monitoring, and observability.

### Guiding Principles

1. **Events are Immutable** — Events describe facts. They are never modified after publication.
2. **Delivery is Infrastructure** — Applications never contain retry loops or replay logic.
3. **Failure is Expected** — Every component assumes external systems will eventually fail.
4. **Observability is First-Class** — Every event is traceable from creation to completion.
5. **Developer Experience Comes First** — Complexity belongs inside the platform, not in applications.
6. **Extensibility Over Configuration** — New delivery mechanisms are implemented as adapters.
7. **Intelligence Must Be Explainable** — Every automated decision exposes why it was made.

---

## MVP Scope

This document describes the full platform vision. Not all of it is being built in the initial implementation.

The active build target is the MVP defined in [`docs/execution-plan.md`](execution-plan.md). That document contains the 12-week timeline, team split, demo script, and explicit stopping criteria.

### Deferred from MVP

The following are intentionally out of scope for the first implementation:

- **Kubernetes** — Docker Compose only for the demo environment
- **Terraform** — no cloud infrastructure to provision
- **CLI tool** — SDK + dashboard covers all demo and developer needs
- **Multi-language SDKs** — Python SDK only
- **Grafana / Prometheus** — the Dashboard has its own built-in charts
- **OpenTelemetry tracing** — structured JSON logs + dashboard observability is sufficient
- **Email / Slack notifications** — dashboard alert panel replaces this for MVP
- **Multi-tenant support** — single-tenant demo environment only
- **Full Replay Manager** — basic stub; full replay is post-MVP
- **Event Registry** — no formal schema validation at ingestion; event type field on the Event entity is sufficient

### First-Class MVP Components

The **Endpoint Simulator** and the **Live Dashboard** are not optional extras. They are core MVP components:

- The Endpoint Simulator is the fault injection mechanism. Without it, the state machine cannot be driven through its states during a demo.
- The Live Dashboard is the demo centrepiece. It makes the research contribution — AEDE's adaptive policy decisions — visible in real time. A demo without the dashboard cannot show the explainability records, ERI chart, or state transitions that constitute the research evaluation.

---

## System Layers

```
                   Applications
                         │
                  SDK / REST API
                         │
               ─────────────────────
                    Relay Platform
               ─────────────────────

              Layer 1: Developer Interfaces
          Dashboard  •  CLI  •  SDK  •  API

               ─────────────────────

              Layer 2: Control Plane

          Adaptive Event Delivery Engine (AEDE)

    ┌───────────────────────────────────────────┐
    │  Endpoint Reliability Evaluator (ERI)     │
    │  Endpoint State Machine                   │
    │  Policy Synthesizer                       │
    │  Explainability Engine                    │
    └───────────────────────────────────────────┘

               ─────────────────────

              Layer 3: Messaging Backbone

                      Kafka

               ─────────────────────

              Layer 4: Data Plane

    Dispatcher → Workers → Delivery Adapters
    Retry Manager
    Replay Manager (deferred)
    Notification Service (deferred)
    Webhook Adapter (v1)

               ─────────────────────

              Layer 5: Infrastructure

    PostgreSQL  •  Redis  •  Docker
    Prometheus (deferred)  •  Grafana (deferred)
    Kubernetes (deferred)  •  Terraform (deferred)
```

---

## Control Plane / Data Plane Separation

### The Fundamental Law

**The Control Plane decides. The Data Plane executes. This separation is inviolable.**

### Control Plane

Responsibilities:
- Evaluate endpoint behavior continuously
- Compute the Endpoint Reliability Index (ERI)
- Drive the Endpoint State Machine
- Synthesize immutable Delivery Policies
- Produce Explainability Records for every decision
- Publish policy updates consumed by the Data Plane
- Operational alerting and administrator notifications
- Dashboard and admin APIs

The Control Plane answers: *Is this endpoint healthy? Should delivery continue? Should retries be delayed? Should the endpoint be quarantined? Should administrators be notified?*

### Data Plane

Responsibilities:
- Event ingestion and validation
- Kafka publishing
- Dispatching events to workers
- Executing Delivery Policies (HTTP, signing, serialization)
- Collecting and publishing telemetry
- Retry scheduling (intervals come from policy, not workers)
- Replay execution

The Data Plane answers: *Which event should be delivered? Has delivery succeeded? Should this event move to the retry topic?*

### Why Separate Them?

- **Scalability**: Workers scale independently from policy services.
- **Extensibility**: New transports do not require changes to policy logic and vice versa.
- **Testability**: Given identical telemetry, AEDE must always produce identical policies. Workers can be benchmarked independently.
- **Explainability**: Every action is traceable — inspect endpoint state and policy before observing the executed action.

---

## Adaptive Event Delivery Engine (AEDE)

AEDE is the decision-making core. It does not deliver events. It continuously evaluates endpoint operational state and synthesizes delivery policies that the Data Plane consumes.

### Design Philosophy

Conventional systems react at the request level:

```
Request → Failed → Retry
```

AEDE reasons about endpoint behavior over time:

> "What does this failure tell us about the current operational state of this endpoint?"

### Inputs

AEDE receives externally observable telemetry only — no access to subscriber infrastructure:

- HTTP response codes and latency
- Connection timeouts, TLS errors, DNS failures
- Consecutive failures and consecutive successes
- Retry history, queue depth, delivery throughput
- Historical availability

### Output: Delivery Policy

AEDE synthesizes an immutable `DeliveryPolicy` object:

```
DeliveryPolicy {
    policy_id
    endpoint_id
    endpoint_state
    eri_score
    retry_policy        → interval, max_count, backoff_strategy, deadline
    concurrency_policy  → max_parallel, throughput_limit, queue_priority
    isolation_policy    → continue | reduce | pause | quarantine
    replay_policy       → allowed, window, batch_size, speed
    notification_policy → notify_admin, alert_level, suppress
    integrity_policy    → hmac_required, signature_ttl, replay_protection
    explainability      → reason, metrics, thresholds, applied_policies
}
```

The Delivery Policy is the contract between the Control Plane and the Data Plane.

### Determinism Requirement

Given identical endpoint observations, identical configuration, and identical policy rules, AEDE must always generate identical Delivery Policies. Determinism is a fundamental design requirement enabling reproducibility and experimental comparison.

---

## Endpoint Reliability Index (ERI)

The ERI is a deterministic composite score (0–100) representing observed endpoint reliability. It is the sole input to state machine transitions.

- **100** = perfectly healthy endpoint
- **0** = endpoint considered unavailable

### Observable Metrics and Default Weights

| Metric | Default Weight |
|---|---:|
| Success Rate | 35% |
| Average Response Latency | 20% |
| Consecutive Failures | 20% |
| Timeout Rate | 10% |
| HTTP 5xx Rate | 5% |
| Queue Depth | 5% |
| Recovery Trend | 5% |

Weights are configurable. The weighting model itself is not the research contribution — the contribution is how the resulting score drives runtime policy decisions.

### Sliding Observation Window

**MVP: attempt-count-based window only.**

ERI is computed over the last N delivery attempts (default: 100). No time-based filtering is applied in the MVP implementation.

This is a deliberate choice to preserve strict determinism. A time-based window (e.g., "last 5 minutes") means two evaluations run at slightly different wall-clock offsets include different telemetry records and produce different ERI scores from identical history — which breaks reproducibility for benchmark experiments. An attempt-count window is a pure function of the telemetry set; the same 100 records always produce the same ERI.

The tradeoff: a count-based window responds differently for high-volume vs low-volume endpoints. An endpoint with 100 attempts in the last 30 seconds gets a very recent window; one with 100 attempts over the last week gets a stale one. This is accepted for MVP. A time-bounded secondary filter (with the cutoff passed as an explicit parameter, never read from wall-clock) is noted as a post-MVP enhancement — see ADR 0009.

> **Determinism rule**: AEDE never calls `datetime.now()` or `time.time()` internally for any policy-relevant computation. Any time boundary needed must be passed as an explicit parameter by the caller. This is enforceable at code review.

### Explainability

Every ERI evaluation exposes: computed score, individual metric values, metric contributions, previous state, new state, and triggering thresholds.

---

## Endpoint State Machine

Each endpoint exists in exactly one operational state at all times.

```
AVAILABLE
    │
    ▼
OBSERVED_DEGRADATION
    │
    ▼
UNSTABLE
    │
    ▼
QUARANTINED
    │
    ▼
RECOVERING
    │
    ▼
AVAILABLE
```

### State Definitions

**AVAILABLE** — Operating normally. High success rate, low latency, stable behavior.
- Policy: immediate delivery, normal concurrency, standard monitoring.

**OBSERVED_DEGRADATION** — Early warning. Increasing latency, isolated failures, elevated timeout frequency.
- Policy: increase monitoring frequency, moderate retry backoff, reduce parallel delivery.

**UNSTABLE** — Persistent instability. Repeated failures, poor success rate, sustained latency increase.
- Policy: aggressive backoff, suspend parallel deliveries, queue future events, notify administrator.

**QUARANTINED** — Delivery intentionally suspended. Endpoint considered unavailable.
- Policy: stop delivery, preserve all events, await recovery evidence, permit manual replay.

**RECOVERING** — Endpoint recovering but stability not yet confirmed.
- Policy: gradual traffic restoration, conservative retry intervals, increased monitoring.

### Transition Philosophy

Transitions require three properties:
1. **Evidence-Based** — State changes require sustained evidence, not isolated events.
2. **Deterministic** — Identical observations always produce identical transitions.
3. **Conservative Recovery** — Recovery requires stronger evidence than degradation. Trust failure quickly; trust recovery cautiously.

### Hysteresis

Simple threshold transitions produce oscillation. Hysteresis prevents this by using different thresholds for degradation and recovery:

- Transition INTO degradation: ERI < 80
- Transition BACK to healthy: ERI > 90

Different thresholds for each direction stabilize the state machine while maintaining responsiveness.

### Non-Adjacent Transitions

The state diagram shows a typical progression, but real endpoint behavior is not monotonic. The state machine must define explicit rules for non-adjacent transitions or it will get stuck in unreachable states during testing.

| From | To | Trigger |
|---|---|---|
| AVAILABLE | OBSERVED_DEGRADATION | ERI drops below degradation threshold |
| AVAILABLE | QUARANTINED | Manual admin quarantine |
| OBSERVED_DEGRADATION | UNSTABLE | ERI drops below unstable threshold |
| **OBSERVED_DEGRADATION** | **QUARANTINED** | **ERI collapses below quarantine threshold in a single window — sudden complete failure bypasses UNSTABLE** |
| OBSERVED_DEGRADATION | AVAILABLE | ERI recovers above recovery threshold |
| UNSTABLE | QUARANTINED | ERI drops below quarantine threshold for sustained period |
| UNSTABLE | OBSERVED_DEGRADATION | ERI partially recovers but not to available threshold |
| **QUARANTINED** | **RECOVERING** | Scheduled probe succeeds OR manual admin trigger |
| **QUARANTINED** | **AVAILABLE** | Manual admin force-clear (emergency override only; logged as administrative action) |
| RECOVERING | AVAILABLE | ERI exceeds recovery threshold sustained over recovery window |
| RECOVERING | QUARANTINED | ERI collapses again during recovery — re-quarantine |

The two most important non-adjacent transitions to implement correctly:
- **OBSERVED_DEGRADATION → QUARANTINED**: Handles sudden complete outages without waiting for UNSTABLE accumulation. If an endpoint returns HTTP 503 for 20 consecutive attempts in a single window, it should quarantine immediately rather than stepping through UNSTABLE.
- **RECOVERING → QUARANTINED**: Prevents premature recovery from becoming a cycle. An endpoint that fails again during gradual traffic restoration re-enters quarantine immediately.

All state transitions — including non-adjacent ones — emit an `EndpointStateChanged` event recording the originating state, destination state, ERI value, and triggering condition.

---

## Event Lifecycle (11 Stages)

### Stage 1 — Event Publication

Application publishes via SDK or REST API. From this point the platform owns the delivery lifecycle.

```python
client.publish(event_type="OrderCreated", payload={...})
```

### Stage 2 — Event Ingestion

The Ingestion Service performs: authentication, authorization, schema validation, metadata enrichment, event ID generation, timestamp assignment. The event is persisted before any delivery occurs.

### Stage 3 — Durable Event Storage

Validated events are appended to Kafka. The platform never delivers transient in-memory events. Every delivery begins from durable storage.

### Stage 4 — Dispatch

The Dispatcher consumes events from Kafka, identifies the destination endpoint, requests the current Delivery Policy from AEDE, creates a DeliveryAttempt, and assigns work to a Worker.

### Stage 5 — Policy Evaluation

Before every DeliveryAttempt, the Dispatcher requests the latest Delivery Policy from AEDE. Policy considers: endpoint state, ERI, recent failures, retry history, queue depth, endpoint configuration. The resulting policy is immutable for that attempt.

### Stage 6 — Delivery

Workers execute Delivery Policies: HMAC generation, HTTP request construction, payload serialization, signature generation, execution, response collection. Workers never make runtime decisions.

### Stage 7 — Telemetry Collection

After delivery, workers publish telemetry (HTTP status, latency, timeout, TLS failures, retry count, response headers). Workers do not interpret telemetry — they report observations.

### Stage 8 — Endpoint Evaluation

AEDE consumes telemetry, recomputes the ERI, and if warranted triggers state transitions and activates new Delivery Policies. Historical DeliveryAttempts are never modified.

### Stage 9 — Failure Handling

```
Worker → Retry Manager → Kafka Retry Topic → Dispatcher → AEDE → Updated Policy → New DeliveryAttempt
```

Retries are entirely new DeliveryAttempts. Previous attempts remain immutable.

### Stage 10 — Quarantine

When AEDE determines QUARANTINED state: future deliveries stop, existing events remain durable, new events continue accumulating, administrators are notified. No events are discarded.

### Stage 11 — Recovery

When evidence indicates recovery: endpoint enters RECOVERING, gradual replay begins, successful delivery transitions endpoint back to AVAILABLE. Replay storms are avoided through Replay Policy rate controls.

### Lifecycle Guarantees

- Every Event receives a globally unique identifier.
- Every DeliveryAttempt receives its own identifier.
- Every PolicyEvaluation receives its own identifier.
- Every state transition is recorded.
- Every automated decision is explainable.
- Every failed delivery remains recoverable.
- No event disappears silently.

---

## Delivery Policy Model

A Delivery Policy answers every operational question before execution begins. Workers should never need to ask "should I retry?" or "should I pause?" — the answer already exists in the policy.

### Policy Structure

```
DeliveryPolicy
├── Policy Metadata       (id, version, timestamp, endpoint_id, state, eri)
├── Retry Policy          (initial_delay, max_delay, max_retries, backoff, deadline)
├── Concurrency Policy    (max_parallel, queue_priority, throughput_limit)
├── Isolation Policy      (continue | reduce_concurrency | pause | quarantine)
├── Replay Policy         (allowed, window, batch_size, speed)
├── Notification Policy   (notify_admin, alert_level, suppress)
├── Integrity Policy      (hmac_required, signature_ttl, replay_protection)
└── Explainability        (reason, metrics, thresholds, applied_policies, eri)
```

### Policy Versioning

Policies are versioned. Future AEDE versions may introduce additional fields. Workers ignore unknown fields, allowing policy evolution without breaking existing execution components.

---

## Domain Model

### Primary Entities

| Entity | Responsibility |
|---|---|
| **Event** | Immutable business fact (OrderCreated, PaymentSucceeded, etc.). Never modified after publication. |
| **Endpoint** | A delivery destination. v1: HTTP webhook URLs. Owns configuration only, not runtime state. |
| **EndpointState** | Operational classification derived from ERI. Computed, never manually assigned. |
| **Delivery** | The platform's responsibility to deliver one Event to one Endpoint. One Event → multiple Deliveries. |
| **DeliveryAttempt** | A single execution against an Endpoint. Immutable. Each retry is a new attempt. |
| **DeliveryPolicy** | Immutable operational contract from AEDE. Consumed by workers. |
| **PolicyEvaluation** | One AEDE execution. Immutable. Produces DeliveryPolicy + ExplainabilityRecord. |
| **Telemetry** | Observations from DeliveryAttempts. Never interpreted by workers. |
| **ExplainabilityRecord** | Immutable record of metrics, thresholds, and reasoning for every policy decision. |
| **Notification** | Operational alert (endpoint quarantined, replay completed, etc.). Always originates from policy. |

### Domain Relationships

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

---

## Subscription Model

Subscriptions decouple event producers from delivery destinations. Applications publish events. Subscriptions determine which endpoints receive them.

A Subscription owns: event types, destination endpoint, enable/disable state, delivery preferences, event filtering (by type, version, tenant in v1).

A Subscription does NOT own: retry logic, endpoint health, delivery policies, endpoint state.

### Subscription Lifecycle

```
CREATED → ACTIVE → PAUSED → DISABLED (post-MVP) → ARCHIVED (post-MVP)
```

Subscriptions are versioned. Historical deliveries always reference the subscription version that created them.

---

## Internal Event Model

The platform is itself event-driven. Internal components communicate via immutable operational events on Kafka rather than synchronous service calls.

### Event Categories

| Category | Examples |
|---|---|
| Delivery Events | DeliveryStarted, DeliverySucceeded, DeliveryFailed, DeliveryTimedOut |
| Policy Events | PolicyEvaluated, PolicyUpdated, EndpointQuarantined, EndpointRecovered |
| Endpoint Events | EndpointStateChanged, EndpointRegistered, EndpointPaused, EndpointResumed |
| Replay Events | ReplayRequested, ReplayStarted, ReplayCompleted, ReplayCancelled |
| Operational Events | NotificationSent, AlertRaised, QueueSaturated, WorkerStarted |

### Correlation and Causation IDs

Every business Event receives a **Correlation ID** inherited by all internal events generated while processing it. This enables complete delivery timeline reconstruction.

**Causation IDs** identify the immediate parent event, enabling dependency graph reconstruction.

---

## Service Architecture

### Control Plane Services

| Service | Responsibility |
|---|---|
| **AEDE** | ERI computation, state machine execution, policy synthesis, explainability records |
| **Event Registry** | Stores event type schemas and metadata; validates event types at ingestion |
| **Endpoint Registry** | Static endpoint configuration (URL, auth, signing keys, transport type) |
| **Subscription Service** | Routing rules, event filtering, subscription versioning |
| **Notification Service** | Operational alerts (email in v1; Slack/PagerDuty in future) |
| **Audit & Explainability Service** | Immutable operational history, policy evaluations, delivery timelines |

### Data Plane Services

| Service | Responsibility |
|---|---|
| **Event Ingestion API** | Auth, validation, event persistence, Kafka publication |
| **Dispatcher** | Kafka consumption, policy fetching, DeliveryAttempt creation, worker assignment |
| **Worker Pool** | Stateless execution: HTTP, signing, serialization, telemetry publication |
| **Retry Manager** | Retry scheduling. Never computes intervals — intervals come from DeliveryPolicy. |
| **Replay Manager** | Creates new DeliveryAttempts from historical events. Never mutates history. |

### Service Communication Rules

1. Services own their own data. No service writes directly into another's datastore.
2. Operational events preferred over synchronous calls where eventual consistency is acceptable.
3. Every externally visible operation emits an audit event.
4. Control Plane computes. Data Plane executes. Always.

---

## Failure Model & Reliability Guarantees

### Failure Domains

**Endpoint Failures** (HTTP 500/503, timeout, TLS error, DNS failure) — affect only the target endpoint. Platform remains healthy.

**Worker Failures** (crash, OOM) — events remain durable in Kafka. Workers are replaceable. No DeliveryAttempt permanently lost.

**Kafka Failures** (broker unavailable, partition election) — platform stops accepting new events rather than risking durability. Availability never preferred over durability.

**Control Plane Failures** (AEDE unavailable) — Workers follow a three-tier policy fallback. AEDE is not a single point of failure:

```
Tier 1: AEDE live policy          (normal operation)
         ↓ if AEDE unreachable
Tier 2: Redis-cached policy       (last known good policy, per endpoint, TTL: 5 minutes)
         ↓ if cache cold or TTL expired
Tier 3: Static fallback policy    (safe hardcoded defaults — Strategy A equivalent)
```

**Redis cache TTL**: Every policy written to Redis expires after **5 minutes** (configurable via `POLICY_CACHE_TTL_SECONDS`, default 300). After expiry the cache entry is gone; the next policy fetch falls through to Tier 3. This prevents a "AVAILABLE — deliver immediately" policy cached before an AEDE outage from being served indefinitely as an endpoint degrades underneath it. 5 minutes is conservative: short enough that a cached policy reflects reasonably recent endpoint state, long enough to survive brief AEDE restarts or network blips without unnecessary Tier 3 degradation.

**Tier 3 — Static Fallback Policy** is a conservative, pre-configured policy that requires no AEDE dependency:

```yaml
fallback_policy:
  retry:
    initial_delay_seconds: 5
    backoff_multiplier: 2.0
    max_delay_seconds: 300
    max_retries: 10
  concurrency:
    max_parallel: 1          # conservative — no endpoint state knowledge
  isolation:
    action: continue         # never quarantine without AEDE confirmation
  notification:
    notify_admin: true       # alert that AEDE is unavailable
```

The static fallback is functionally equivalent to Strategy A in the research evaluation — it is the known, safe, documented baseline. Delivery continues; adaptive intelligence is suspended. Operators are alerted. When AEDE recovers, it re-evaluates all active endpoints and resumes normal policy synthesis.

This design means an AEDE outage is an operational degradation event, not a platform outage.

**Dashboard Failures** — never affect delivery. Dashboard is strictly read-only and outside the delivery path.

### Delivery Guarantees

The platform guarantees:
- At-Least-Once Delivery
- Durable Event Storage (persist before deliver)
- Deterministic Policy Evaluation
- Replay Support
- Explainable Runtime Decisions
- Complete Delivery Timeline

The platform does NOT guarantee:
- Exactly-Once Delivery
- Ordered Delivery Across Endpoints
- Zero Duplicate Deliveries

Consumers must implement idempotent processing.

### Failure Escalation

```
Delivery Failure → Retry → Endpoint Degradation → Endpoint Isolation
→ Quarantine → Administrator Notification → Manual Replay
```

The platform never immediately escalates to manual intervention.

---

## Feature Flags for Research

Feature flags enable switching delivery strategies without redeployment. This is the mechanism for benchmarking Strategy A vs B vs C.

| Flag | Effect |
|---|---|
| `AEDE_ENABLED` | Full adaptive policy engine vs fallback |
| `ERI_ENABLED` | ERI-driven state machine vs static health |
| `STATE_MACHINE_ENABLED` | Full 5-state model vs binary healthy/unhealthy |
| `STATIC_RETRY_MODE` | Fixed retry interval (baseline Strategy A) |
| `ADAPTIVE_RETRY_MODE` | Adaptive backoff without state model (baseline Strategy B) |

Benchmark flow: configure flags → run benchmark suite → collect metrics → compare.

---

## Configuration Philosophy

No hardcoded thresholds. Everything externalized:

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

---

## Engineering Philosophy

The Event Delivery Platform is not a webhook service. It is not a retry library. It is not a message broker. It is not a workflow engine.

It is an operational control system for reliable outbound event delivery.

Applications describe **what happened**.

The platform determines **how delivery should occur**.

Every architectural decision should reinforce that separation.

---

## Non-Goals (Version 1)

These are intentionally outside scope. They are deliberate decisions, not missing features.

| Non-Goal | Reason |
|---|---|
| **Exactly-Once Delivery** | Cannot be guaranteed across external organizational boundaries without subscriber cooperation. At-least-once with idempotent consumers is the correct model. |
| **Workflow Orchestration** | The platform ends its responsibility once reliable delivery is complete. Business workflows remain in application logic (use Temporal, etc. for that). |
| **Event Processing** | The platform delivers events. It does not transform, aggregate, enrich, or analyze them. |
| **Message Broker Replacement** | Kafka remains the durable backbone. This project builds upon existing messaging infrastructure, not replaces it. |
| **API Gateway** | Not a reverse proxy. No inbound request routing, rate limiting, or authentication gateway. |
| **Enterprise Identity Platform** | Authentication exists only to support platform operation. Full RBAC, SSO, and organizational identity management are v1 non-goals. |
| **Machine Learning Decisions** | Adaptive delivery decisions remain deterministic and explainable. ML may be explored in future research but is explicitly excluded from v1. |
| **Managed SaaS** | The initial goal is a self-hostable open-source platform. Cloud-hosted commercial deployment is out of scope. |

---

## Architectural Principles

These principles are the constitution of the platform. Every decision, PR, and design proposal is evaluated against them. If a change violates a principle, the burden of proof lies with the change.

**Principle 1 — Reliability Before Features**: A feature that compromises delivery reliability is not an improvement. Correct delivery always takes precedence.

**Principle 2 — Explainability Before Automation**: Every automated decision must be explainable. The platform always answers: *Why was this decision made?* Automation without explanation is unacceptable.

**Principle 3 — Determinism Before Intelligence**: Version 1 favors deterministic policy evaluation over opaque ML. The same observations always produce the same delivery policy. Predictability builds trust.

**Principle 4 — Explicit State Over Hidden State**: Every important operational state exists as an explicit domain concept. Nothing important lives only in memory. Everything important is observable.

**Principle 5 — Events Are Immutable**: Business Events, Operational Events, Delivery Attempts, and Policy Evaluations are all immutable. History is never rewritten.

**Principle 6 — Policies Decide, Workers Execute**: Decision making belongs exclusively to the Control Plane. Execution belongs exclusively to the Data Plane. Workers never contain business logic.

**Principle 7 — Infrastructure Is Replaceable**: The platform depends on abstractions, not infrastructure. Kafka, PostgreSQL, Prometheus, and cloud providers should all be replaceable without redesigning the platform.

**Principle 8 — Every Failure Must Produce Knowledge**: Every failure generates telemetry, metrics, logs, explainability records, and operational events. Nothing fails silently.

**Principle 9 — Developer Time Is Valuable**: The platform exists to eliminate operational work. If developers repeatedly solve the same infrastructure problem manually, the platform should eventually solve it automatically.

**Principle 10 — Open Source First**: Features prioritize openness, extensibility, and maintainability over proprietary optimization.

**Principle 11 — Benchmark Before Claim**: Every performance or reliability claim is supported by reproducible experimental evidence. Assumptions are not results.

**Principle 12 — Architecture Over Technology**: Technology choices will change. Architecture should remain stable. The project should be recognizable even if every implementation technology changes.

---

## Engineering Standards

### Core Principles

1. **Explicit over implicit** — System behavior should always be obvious. Avoid hidden side effects, implicit state changes, and magic configuration.
2. **Immutability by default** — Events, DeliveryAttempts, DeliveryPolicies, PolicyEvaluations, ExplainabilityRecords, and Telemetry are always immutable.
3. **Deterministic behavior** — Same inputs always produce same outputs. No randomness in operational behavior.
4. **Explainability first** — Every automated decision answers: why, which metrics, which thresholds, which policies.
5. **Single responsibility** — Every class owns one responsibility. Every service owns one capability.
6. **Composition over inheritance** — Delivery Adapters implement interfaces, not inherit behavior.
7. **Interface-driven** — Components communicate through contracts, never concrete implementations.
8. **Stateless workers** — Workers remain stateless. Persistent state belongs in databases. Decisions belong in AEDE.
9. **Event-driven communication** — Where eventual consistency is acceptable, prefer events over synchronous service chains.
10. **Every failure is observable** — No exception disappears. Every failure produces structured logs, metrics, and operational events.

### Logging Standards

All logs are structured JSON. Every entry includes:
- timestamp, service_name, correlation_id, event_id, endpoint_id, delivery_attempt_id, log_level, message

### Error Handling

Never swallow exceptions. Every error must be: classified, logged, measured, reported, and recoverable where possible.

### Testing Philosophy

```
Unit Tests (AEDE has highest coverage)
    ↓
Integration Tests
    ↓
End-to-End Tests
    ↓
Failure Simulation Tests
```

### AI Development Guidelines

AI coding assistants working on this codebase must:
- Preserve architectural boundaries (CP/DP separation)
- Never bypass AEDE — workers do not compute retry intervals or isolation decisions
- Never embed business logic in workers
- Never introduce hidden state
- Generate deterministic implementations
- Maintain explainability — every decision must produce an ExplainabilityRecord

### Definition of Done

A feature is complete only when all of the following are true:
- [ ] Unit tested
- [ ] Integration tested
- [ ] Structured JSON logs emitted
- [ ] Prometheus metrics registered
- [ ] Every automated decision produces an ExplainabilityRecord
- [ ] Failure modes identified and handled
- [ ] No hidden state introduced
- [ ] Control Plane / Data Plane boundary respected
- [ ] Compatible with feature flag switching
- [ ] Documented (explains why, not just what)
- [ ] Reviewed

Functionality alone is not sufficient.
