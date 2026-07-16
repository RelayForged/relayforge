# Relay — Research Document

## Problem Statement

Modern software systems rely on webhooks to notify external systems of business events. Despite appearing simple, webhook delivery crosses organizational and infrastructural boundaries the sender cannot control: network latency, endpoint availability, server configuration, rate limits, deployment schedules, maintenance windows.

Failures are therefore **expected**, not exceptional.

Yet webhook delivery is typically implemented as a simple HTTP request followed by a retry loop:

```python
try:
    requests.post(url, data=payload)
except:
    retry()
```

This approach fails in production. As applications scale, developers independently rebuild the same supporting infrastructure: retry queues, replay mechanisms, dead letter queues, delivery dashboards, monitoring, alerting. These components are re-implemented inconsistently across teams, resulting in duplicated effort and limited reliability guarantees.

Applications should not be responsible for managing event delivery. Reliable outbound event delivery should be treated as infrastructure — just as developers do not build their own databases, they should not repeatedly implement reliable delivery systems.

---

## Research Context

This project lies at the intersection of five research domains:

**Distributed Systems** — Applies distributed systems principles (resilience, graceful recovery, eventual consistency) to outbound event delivery. Rather than assuming endpoints are available, the system assumes they will eventually fail.

**Event-Driven Architecture** — Extends EDA principles beyond internal communication by treating outbound webhook delivery as another stage in a durable event pipeline.

**Stream Processing** — Adopts Kafka's durable log model. Workers consume immutable events from Kafka, enabling deterministic replay and simplifying failure recovery.

**Cloud-Native Infrastructure** — Containerization, Kubernetes orchestration, Infrastructure as Code, and immutable deployments ensure reproducibility across environments.

**Developer Experience** — SDKs, dashboards, replay tools, debugging workflows, and observability are first-class capabilities rather than optional additions.

---

## Research Gap

### Literature Position

Prior work generally falls into five categories:

1. **Adaptive Retry and Circuit Breaking** — Adaptive controllers outperform static retry in service meshes by adjusting backoff based on runtime behavior. These systems assume control over both communicating services.

2. **Endpoint Health and Failure Detection** — ML and statistical techniques achieve accurate anomaly detection using telemetry from fully instrumented, operator-controlled infrastructure.

3. **Distributed Messaging and Stream Processing** — Systems such as Kafka solve durable event storage, partitioning, and replay. They intentionally stop at reliable event transport.

4. **Cloud Security** — Mature approaches for authentication, message integrity, cryptographic verification, and replay prevention. Operate independently from runtime delivery decisions.

5. **Developer Tooling** — Commercial webhook platforms (Svix, Hookdeck, etc.) provide production-ready delivery and dashboards. Implementations are proprietary with no insight into internal adaptive behavior.

### The Compound Gap

Each individual problem domain has received significant attention. What remains largely unexplored is the **integration of these concepts into a unified runtime decision framework specifically designed for outbound event delivery**.

To the best of our knowledge, no prior work simultaneously:

- continuously evaluates endpoint state using externally observable behavior only,
- derives runtime delivery policies directly from that state,
- coordinates retry scheduling, endpoint isolation, replay decisions, and operational alerts through a common policy engine,
- exposes these decisions transparently through developer-facing observability,
- and provides a reproducible platform for evaluating those policies under controlled failure scenarios.

### Why Existing Adaptive Systems Cannot Be Directly Applied

**Constraint 1 — External Ownership**: Adaptive controllers in service meshes assume control over both communicating services. Webhook delivery targets systems owned by external organizations whose infrastructure cannot be modified, instrumented, or controlled.

**Constraint 2 — Limited Observability**: Service meshes rely on distributed traces, sidecar proxies, and kernel telemetry. Webhook platforms observe only externally visible signals: HTTP response codes, response latency, network timeouts, connection failures, TLS errors. The platform must infer endpoint state from significantly weaker signals.

**Constraint 3 — No Traffic Control**: When internal services degrade, service meshes reroute traffic or drain instances. Webhook endpoints provide no such control. The delivery platform can only decide whether, when, and how frequently to attempt delivery.

**Constraint 4 — External Failure Amplification**: Aggressive retries against internal services consume operator-owned infrastructure. Aggressive retries against webhook endpoints directly impact subscriber infrastructure and may amplify outages or violate rate limits. The platform has an obligation to balance reliability with responsible delivery behavior.

---

## Research Hypothesis

> Runtime delivery policies derived from continuously evaluated endpoint state can improve delivery efficiency, reduce unnecessary retries, decrease administrative intervention, and increase system resilience when compared with conventional static retry strategies, while maintaining complete transparency and reproducibility.

---

## Experimental Methodology & Evaluation Framework

### Research Question

Can adaptive runtime delivery policies driven by endpoint state outperform conventional static retry mechanisms in webhook-based event delivery systems under realistic partial failure conditions?

### Three Experimental Strategies

**Strategy A — Static Retry (Baseline)**

Fixed retry interval regardless of endpoint state.

```
Attempt → Failed → Wait 5s → Retry → Wait 5s → Retry → ...
```

**Strategy B — Adaptive Retry (Intermediate Baseline)**

Retry interval adjusts dynamically based on observed failures. No endpoint state model. No unified policy engine.

**Strategy C — AEDE (Proposed System)**

Runtime policy synthesis driven by:
- Endpoint Reliability Index (ERI)
- Endpoint State Machine (5 states)
- Delivery Policies (retry, concurrency, isolation, replay, notification, integrity)
- Explainability Engine

Only the delivery strategy changes between experiments. All other variables (hardware, Kafka configuration, worker count, payload size, event volume, endpoint implementation, authentication, network topology) remain identical.

### Metrics (Dependent Variables)

| Metric | Description |
|---|---|
| **Delivery Success Rate** | Percentage of events successfully delivered |
| **Average Retry Count** | Average DeliveryAttempts required per Event |
| **Event Completion Time** | Time from Event publication to successful delivery |
| **Recovery Time** | Time from endpoint degradation to resuming normal delivery |
| **Delivery Throughput** | Events delivered per second |
| **Queue Growth** | Retry queue growth during sustained endpoint failures |
| **Quarantine Frequency** | Number of endpoint quarantines triggered |
| **Replay Duration** | Time to replay accumulated events after recovery |
| **Administrative Intervention** | Manual actions required (lower = more autonomous) |
| **Explainability Coverage** | Percentage of automated decisions with complete explanations (target: 100%) |

### Failure Scenarios

| Scenario | Description |
|---|---|
| 1 | Intermittent Timeouts — random request timeouts |
| 2 | High Latency — gradually increasing response latency |
| 3 | Burst Failures — large numbers of consecutive failures |
| 4 | Endpoint Outage — endpoint unavailable for extended periods |
| 5 | Partial Recovery — alternating success and failure |
| 6 | Credential Failure — expired authentication credentials |
| 7 | TLS Failure — certificate validation errors |
| 8 | Rate Limiting — HTTP 429 responses |
| 9 | Network Partition — temporary communication loss |
| 10 | Slow Degradation — endpoint gradually becomes unstable (best demonstrates AEDE advantage) |

### Experimental Reproducibility

Every experimental run records:
- Platform version and policy version
- Complete configuration (thresholds, weights, flags)
- Random seed for failure injection
- Event dataset and volume
- Timestamp

Results are independently reproducible by future researchers.

### Statistical Analysis

Multiple runs per experiment. Reported metrics include mean, median, standard deviation, and 95% confidence intervals. Statistical significance tests validate observed improvements where appropriate.

### Threats to Validity

**Internal validity**: Policy thresholds influence results. Experiments evaluate multiple threshold configurations.

**External validity**: Benchmark environment may not perfectly represent production workloads. Failure scenarios model realistic industrial conditions where possible.

**Construct validity**: Metrics directly measure delivery performance rather than implementation artifacts.

**Reproducibility**: All configurations, datasets, policies, and infrastructure published alongside source code to enable independent verification.

---

## Expected Outcomes

The proposed AEDE is expected to:
- Reduce unnecessary retry attempts
- Reduce queue growth during endpoint degradation
- Improve recovery stability
- Reduce operator intervention
- Increase delivery efficiency
- Maintain 100% explainability coverage
- Preserve deterministic behavior

These claims must be validated experimentally, not assumed.

---

## What This Work Is Not Claiming

Before stating the contributions, it is important to be precise about what this work does **not** claim:

- **The ERI weights are not claimed to be optimal.** The weights (35% success rate, 20% latency, etc.) are a documented design parameter — a reasonable starting configuration. The weighting model itself is not the research contribution. Optimal weight discovery would require a separate experiment with access to ground-truth failure labels, which is out of scope.
- **AEDE is not a machine learning system.** No model is trained. No parameters are learned from data. The engine is deterministic and rule-based by design (see ADR 0004).
- **This work does not propose a new retry algorithm.** Strategy B (adaptive backoff) is the closest existing approach. AEDE's contribution is the layer above the retry interval — the state model and policy framework that determines *when and whether* to retry at all.

## Primary Research Contribution

The primary contribution is the **Adaptive Event Delivery Policy Framework**: a unified runtime system that couples continuous endpoint-state evaluation to runtime delivery policy synthesis in the specific constraints of outbound webhook delivery.

The experimental claim is: **having a state model + policy engine (Strategy C) produces measurably better delivery outcomes than adaptive backoff without state (Strategy B), which in turn outperforms static retry (Strategy A)**, under realistic partial failure conditions.

This is a systems/architecture contribution. The ERI weights and state machine thresholds are hyperparameters of the implementation, disclosed and held constant during the comparison experiments. The object of study is whether the framework architecture improves delivery outcomes — not whether any particular weight configuration is optimal.

## Scientific Contributions

1. **A state-driven model for endpoint health using only externally observable signals** — endpoints cannot be instrumented or controlled; the ERI and 5-state machine derive health from HTTP response codes, latency, and connection failures alone. Prior work in adaptive retry assumes operator control over both sides of the communication.

2. **A policy framework that coordinates retry, isolation, replay, and notification through a single unified engine** — prior work addresses these concerns in isolation. This work connects them through a common policy object derived from endpoint state, and demonstrates that coordination produces better outcomes than handling each concern independently.

3. **A reproducible open-source implementation** — the complete Relay platform, including infrastructure, configuration, and benchmark tooling, published for independent verification and extension.

4. **A controlled fault injection framework for comparing adaptive and static delivery strategies** — a reusable evaluation environment enabling future research into event delivery policies.

## Secondary Contribution (Weight Sensitivity Analysis)

If time permits, a lightweight weight sensitivity analysis should be included as a secondary experiment: test 3–4 alternative ERI weight configurations and show that the framework's benefit over Strategy A and B holds across configurations, even if absolute performance varies. This preempts the obvious panel question ("how do you know these weights are any good?") without requiring full weight optimization. The claim would be: *the framework's advantage is robust to reasonable variation in the ERI weights*, not that any specific configuration is optimal.

This is a bonus section, not a primary contribution. Do not block the primary evaluation on it.

## Research Artifacts

1. **Adaptive Event Delivery Engine (AEDE)** — the proposed runtime policy framework.
2. **Event Delivery Platform (Relay)** — the production-quality implementation.
3. **Benchmark & Fault Injection Framework** — a reusable evaluation environment for future research into adaptive event delivery.
