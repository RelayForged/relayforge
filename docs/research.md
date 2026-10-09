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

Each individual problem domain has received significant attention. What remains largely unexplored in the **published literature** is the integration of these concepts into a unified runtime decision framework specifically designed for outbound event delivery — and the experimental validation of such a framework against conventional approaches.

Commercial webhook platforms (Svix, Hookdeck, etc.) likely implement some form of adaptive delivery internally, but their implementations are proprietary. No published account of their internal decision architecture, policy models, or comparative evaluations exists. The gap addressed by this work is therefore a **research literature gap**, not a claim that no such system has ever been built.

To the best of our knowledge, no prior published work:

- presents a unified policy framework that derives delivery behavior from continuously evaluated endpoint state using only externally observable signals,
- experimentally compares that framework against static and adaptive baselines under controlled failure conditions,
- and publishes the complete implementation — including infrastructure, configuration, and benchmark tooling — for independent verification and extension.

The framework coordinates retry scheduling, endpoint isolation, replay decisions, and operational alerts through a common policy engine, and exposes every automated decision transparently through developer-facing observability. These capabilities are described in detail in the System Design sections; the research contribution is their integration, experimental evaluation, and open publication.

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
| 11 | Bursty Traffic Under Degradation — two burst windows dropping success rate to 40% then 20% across 170 events; supplementary, calibration-only check of ERI sliding-window reactivity |
| 12 | Re-Quarantine Relapse — recovering endpoint suffers secondary failure spike testing fast re-isolation |
| 13 | Rapid Micro-Flapping — high-frequency oscillations testing hysteresis dampening and transition bounds |
| 14 | Long Sustained Mild Degradation — prolonged mild degradation (75% success across 400 events) testing conservative degradation handling |

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

**Internal validity — threshold and weight sensitivity**: AEDE's policy decisions depend on configured ERI weights and state machine thresholds. Different configurations may produce different relative performance between strategies. *Mitigation*: all weights and thresholds are disclosed and held constant across strategy comparisons. An automated empirical weight sensitivity analysis (28 systematic perturbations of $\pm 10\%$ and $\pm 20\%$ across all 7 dimensions) demonstrated 100% stability, zero false quarantines, and preserved state machine hysteresis stability across the entire parameter space.

**Construct validity — calibration vs. evaluation split (weight selection leakage)**: Evaluating weight configurations on the same scenarios used for hypothesis testing risks circular reasoning (training on the test set). *Mitigation*: we enforce a strict partition between the calibration set ($\{1, 2, 6, 7, 8, 11\}$) and the held-out evaluation set ($\{3, 4, 5, 9, 10, 12, 13, 14\}$). Weight calibration (`optimize_weights.py`) and evolutionary search (`genetic_optimizer.py`) observe only the calibration scenarios (including supplementary Scenario 11, which stresses ERI sliding-window reactivity under bursty success-rate degradation). Hypotheses 1, 2, and 3 are evaluated strictly on the held-out set, ensuring headline metrics and statistical tests reflect genuine generalisation to unseen failure modes.

**Internal validity — single-endpoint evaluation**: MVP experiments evaluate AEDE against a single endpoint. Production systems deliver to many concurrent endpoints, where worker contention, shared queue depth, and cross-endpoint policy interactions may affect results. *Mitigation*: a concurrent multi-endpoint contention benchmark (`benchmark/multi_endpoint.py`) models shared worker pools under load from both failing and healthy tenants, evaluating noisy-neighbor isolation directly.

**External validity — synthetic failure patterns**: The Endpoint Simulator produces controlled, parameterized failures. Real-world endpoints exhibit correlated failures, ambiguous partial responses (e.g., HTTP 200 with error body), deployment-window behavior, and rate-limit patterns that may not match the simulator's modes. *Mitigation*: the simulator's four failure modes (http_error, timeout, slow, rate_limit) cover the most common failure signatures observed in production webhook systems. The 14 scenarios (6 calibration, 8 held-out) are designed to be representative, not exhaustive.

**External validity — controlled event volume**: Experiments use a fixed event publication rate. Production systems experience bursty, non-uniform event volumes that interact with queue depth and ERI computation. *Acknowledgment*: variable-rate event publication is a future evaluation dimension.

**Construct validity — ERI as a proxy for endpoint health**: The ERI is a weighted composite of observable metrics, not a direct measurement of endpoint availability. The composite score may mask important signals (e.g., a high success rate hiding increasing latency) or overweight unimportant ones. *Mitigation*: the ERI sub-scores are preserved in every ExplainabilityRecord, allowing post-hoc analysis of whether the composite score accurately reflected the endpoint's actual condition during each experiment.

**Construct validity — window size sensitivity**: The attempt-count-based ERI window (default: 100 attempts) produces different responsiveness characteristics for high-volume vs low-volume endpoints. A window of 100 attempts represents seconds of history for a high-throughput endpoint but hours for a low-volume one. *Mitigation*: benchmark experiments use controlled, uniform event publication rates, eliminating this asymmetry during evaluation. The tradeoff is acknowledged as a limitation for production deployment (see ADR 0009).

**Reproducibility**: All configurations, event datasets, policy parameters, simulator settings, and infrastructure definitions are published alongside the source code. Experiments use attempt-count-based ERI windows with explicit timestamp parameters (no wall-clock dependency inside AEDE), ensuring identical replay across runs.

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

- **The ERI weights are not claimed to be globally optimal.** They are calibrated via a genetic-algorithm search over the calibration scenario set (§ Threats to Validity), disclosed in full, and re-verified against the held-out evaluation set before adoption — a documented, reproducible calibration, not a hand-picked guess, but also not a claim of global optimality. The weighting model itself is not the research contribution. A fully unconstrained search over the true global objective would require ground-truth failure labels beyond what the current scenario suite provides, which is out of scope.
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

An empirical weight sensitivity analysis (`benchmark/sensitivity_analysis.py`) systematically evaluates 28 perturbations of $\pm 10\%$ and $\pm 20\%$ across all seven ERI weight dimensions against formal stability bounds. The analysis confirms:
- **Zero false quarantines** under nominal and high-latency traffic across all 28 perturbations.
- **Flapping stability preserved**: state transitions in partial recovery (Scenario 5) remain bounded at $\le 5$ transitions.
- **Resilient retry reduction**: outage retry reduction vs Strategy A remains robust ($\ge 50\%$ across all variations).

This demonstrates that the framework's advantage is robust to variation in ERI weights and occupies a wide, resilient basin rather than an overfitted local optimum.

## Future Work: Offline Reinforcement Learning for Weight Optimisation

The current ERI implementation uses static weights discovered by an offline genetic-algorithm search over a fixed calibration scenario set (not hand-picked, but also not learned from live operational data). These weights are disclosed design parameters — defaults calibrated and validated through benchmark experiments, then frozen for deterministic production use. The architecture naturally accommodates a more principled, data-driven weight discovery process through **offline reinforcement learning**, which would replace the fixed calibration scenario suite with real operational telemetry as the training signal.

### Motivation

Different deployment environments exhibit different failure signatures. An endpoint ecosystem dominated by timeout-prone services may benefit from higher `timeout_weight`; one dominated by intermittent 5xx errors may benefit from higher `http_5xx_weight`. Expert-defined weights cannot anticipate every deployment context. A data-driven approach to weight discovery would allow AEDE to adapt its sensitivity profile to the specific failure patterns observed in a given environment.

### Proposed Approach

```
Production (weights v1, frozen, deterministic)
        │
        │  telemetry + delivery outcomes accumulate in PostgreSQL
        ▼
Offline RL Training (reads historical data, explores weight space freely)
        │
        │  produces candidate weight configuration v2
        ▼
Validation (run v2 against benchmark scenarios using Endpoint Simulator)
        │
        │  operator reviews and approves
        ▼
Production (weights v2, frozen, deterministic)
```

The RL agent operates entirely offline, outside the production delivery path:

1. **Data collection**: The production system accumulates telemetry records and delivery outcomes in PostgreSQL as a natural byproduct of normal operation. No additional instrumentation is required — the existing `ExplainabilityRecord` and `DeliveryAttempt` entities already capture the necessary signal.

2. **Offline training**: A batch RL process reads historical telemetry and experiments with alternative weight configurations. The reward signal optimises for a composite objective (e.g., minimise failed deliveries, minimise recovery time, minimise unnecessary retries — with configurable tradeoff coefficients). All exploration happens offline against historical data. No production deliveries are affected.

3. **Validation**: Candidate weight configurations are evaluated against the existing benchmark framework using the Endpoint Simulator. The same controlled failure scenarios (Scenarios 1–10) that validate the current weights are used to validate learned weights. Configurations that degrade performance on any scenario are rejected.

4. **Promotion**: An operator reviews the learned weights, the benchmark results, and the RL training metrics before promoting the new configuration to production. This human-in-the-loop step ensures accountability and prevents pathological weight configurations from reaching production.

5. **Versioning**: Each weight configuration is versioned. `ExplainabilityRecord` includes a `weight_version` field, enabling full traceability: for any historical policy decision, the exact weight configuration that produced it can be retrieved.

### Determinism Guarantee

This approach **fully preserves AEDE's determinism requirement**. At any point in time, the active weight configuration is frozen. Given identical telemetry and identical weights, AEDE produces identical policies. The RL agent never runs during policy evaluation — it runs offline, produces a static configuration artifact, and that artifact is deployed through the same mechanism as a manual `config.yaml` update.

### Why Offline RL, Not Online RL

Online RL (adjusting weights during live delivery) was considered and rejected for three reasons:

1. **Exploration risk**: Online RL requires exploring suboptimal weight configurations during production delivery, deliberately degrading delivery quality for some events to improve future performance. This is unacceptable in an infrastructure system with delivery guarantees.

2. **Determinism erosion**: Online weight updates mean two identical delivery attempts at slightly different times could receive different policies because the weights shifted between them. The determinism guarantee weakens from global to epoch-local, complicating reproducibility and explainability.

3. **Reward signal complexity**: Designing a real-time reward function that correctly balances competing objectives (delivery speed vs. retry efficiency vs. endpoint protection) without causing pathological behaviour is a significant research challenge in its own right — one that is more safely explored offline.

### Scope

This is documented as future work. The current implementation demonstrates that the AEDE architecture produces measurably better delivery outcomes with weights discovered by an offline, scenario-based search. The offline RL extension would demonstrate that the architecture can push this further and *discover* weights from real operational data instead of a fixed synthetic scenario suite — without any structural changes to the policy engine, the explainability system, or the Control Plane / Data Plane boundary.

---

## Research Artifacts

1. **Adaptive Event Delivery Engine (AEDE)** — the proposed runtime policy framework.
2. **Event Delivery Platform (Relay)** — the production-quality implementation.
3. **Benchmark & Fault Injection Framework** — a reusable evaluation environment for future research into adaptive event delivery.
