# Chapter 4 — Evaluation Methodology

## 4.1 Research Question

Can adaptive runtime delivery policies driven by continuously evaluated endpoint state outperform conventional static retry mechanisms in webhook-based event delivery systems under realistic partial failure conditions?

## 4.2 Constraints on Adaptive Delivery in Webhook Systems

Before describing the experimental methodology, it is necessary to establish the constraints that distinguish webhook delivery from internal service-mesh communication. These constraints explain why existing adaptive controllers cannot be directly applied and motivate the design of AEDE.

**Constraint 1 — External Ownership.** Adaptive controllers in service meshes assume control over both communicating services. Webhook delivery targets systems owned by external organisations whose infrastructure cannot be modified, instrumented, or controlled.

**Constraint 2 — Limited Observability.** Service meshes rely on distributed traces, sidecar proxies, and kernel telemetry. Webhook platforms observe only externally visible signals: HTTP response codes, response latency, network timeouts, connection failures, and TLS errors. The platform must infer endpoint state from significantly weaker signals.

**Constraint 3 — No Traffic Control.** When internal services degrade, service meshes reroute traffic or drain instances. Webhook endpoints provide no such control. The delivery platform can only decide whether, when, and how frequently to attempt delivery.

**Constraint 4 — External Failure Amplification.** Aggressive retries against internal services consume operator-owned infrastructure. Aggressive retries against webhook endpoints directly impact subscriber infrastructure and may amplify outages or violate rate limits. The platform has an obligation to balance reliability with responsible delivery behaviour.

---

## 4.3 Research Hypothesis

> Runtime delivery policies derived from continuously evaluated endpoint state can improve delivery efficiency, reduce unnecessary retries, decrease administrative intervention, and increase system resilience when compared with conventional static retry strategies, while maintaining complete transparency and reproducibility.

---

## 4.4 Three Experimental Strategies

The experimental evaluation compares three delivery strategies. Only the delivery strategy changes between experiments. All other variables — hardware, Kafka configuration, worker count, payload size, event volume, endpoint implementation, network topology — remain identical.

### 4.4.1 Strategy A — Static Retry (Baseline)

Fixed exponential backoff regardless of endpoint state. Strategy A does not monitor endpoint health, does not quarantine, and does not adjust concurrency. It always produces the same policy: `initial_delay = 5s`, `max_retries = 10`, `backoff_multiplier = 2.0`, `max_parallel = 1`.

```
Attempt → Failed → Wait 5s → Retry → Wait 10s → Retry → Wait 20s → ...
```

Strategy A also serves as the Tier 3 static fallback policy when AEDE is unavailable. It is the known, safe, documented baseline.

### 4.4.2 Strategy B — Adaptive Retry (Intermediate Baseline)

Retry interval adjusts dynamically based on observed success rate. No endpoint state model, no hysteresis, no quarantine isolation. The initial delay is scaled inversely with the ERI success rate sub-score:

$$
\text{scaled\_delay} = \min\left(\frac{5.0}{\max(\text{success\_rate\_score}, 0.1)}, 300.0\right)
$$

Concurrency scales with success rate: `max_parallel = 5` when success rate > 0.8, `max_parallel = 2` when > 0.4, `max_parallel = 1` otherwise. Strategy B demonstrates that simple reactive adaptation improves over static retry without requiring a full state model.

Strategy B has no concept of quarantine. When an endpoint fails completely ($\text{success\_rate} = 0$), Strategy B continues delivering with maximum backoff (`scaled_delay = 50s`) rather than suspending delivery.

### 4.4.3 Strategy C — AEDE (Proposed System)

Runtime policy synthesis driven by four integrated components:

1. **Endpoint Reliability Index (ERI)** — Weighted composite score (0–100) from seven sub-metrics.
2. **Endpoint State Machine** — Five states with hysteresis: AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED → RECOVERING → AVAILABLE.
3. **Policy Synthesiser** — Maps state + ERI to immutable `DeliveryPolicy` objects containing retry, concurrency, isolation, replay, notification, and integrity sub-policies.
4. **Explainability Engine** — Produces an `ExplainabilityRecord` for every policy decision.

Strategy C's key behavioural difference: when an endpoint degrades sufficiently to enter QUARANTINED state (ERI < 30), delivery is entirely suspended (`max_retries = 0`, `IsolationAction = QUARANTINE`). No retry attempts are wasted against a known-unavailable endpoint. Events remain durable in Kafka. When recovery evidence appears, gradual traffic restoration begins through the RECOVERING state.

All three strategies produce `DeliveryPolicy` objects through the same `DeliveryStrategy` interface. Feature flags select which strategy is active at runtime.

---

## 4.5 Metrics (Dependent Variables)

Ten metrics evaluate the three strategies. These are the dependent variables of the experiment:

| # | Metric | Definition | Better = |
|---|---|---|---|
| 1 | **Delivery Success Rate** | Fraction of events successfully delivered | Higher |
| 2 | **Average Retry Count** | Mean retry attempts per event | Lower |
| 3 | **Event Completion Time** | Time from event publication to successful delivery | Lower |
| 4 | **Recovery Time** | Event count from first degradation to return to AVAILABLE state | Lower |
| 5 | **Delivery Throughput** | Events delivered per second | Higher |
| 6 | **Queue Growth** | Retry queue growth during sustained endpoint failures | Lower |
| 7 | **Quarantine Frequency** | Number of endpoint quarantine entries | Context-dependent |
| 8 | **Replay Duration** | Time to replay accumulated events after recovery | Lower |
| 9 | **Administrative Intervention** | Manual actions required (lower = more autonomous) | Lower |
| 10 | **Explainability Coverage** | Percentage of automated decisions with complete explanations (target: 100%) | Higher |

Metrics 1, 2, and 4 are the primary evaluation criteria for the research hypothesis. Metric 2 (Average Retry Count) is the most direct measure of the hypothesis: AEDE should waste significantly fewer retries by recognising and isolating degraded endpoints rather than retrying blindly.

---

## 4.6 Failure Scenarios (Independent Variable)

Ten failure scenarios constitute the independent variable. Each scenario is a time-sequenced fault injection profile — a sequence of phases, where each phase specifies the endpoint's behaviour (success rate, latency, timeout rate, HTTP 5xx rate) for a defined number of delivery attempts.

| # | Scenario | Key Characteristic | Primary ERI Sub-Metric Affected |
|---|---|---|---|
| 1 | Intermittent Timeouts | 30% random timeout failure, otherwise healthy | Timeout rate, latency |
| 2 | High Latency | Response time gradually increases 100ms → 4500ms | Latency score |
| 3 | Burst Failures | Sudden 100% failure for 60 events, then full recovery | Success rate, consecutive failures |
| 4 | Endpoint Outage | Extended 100% unavailability (150 events) | All sub-metrics |
| 5 | Partial Recovery | Alternating success and failure, never fully recovers | Recovery trend |
| 6 | Credential Failure | HTTP 401 responses — success rate drops to 0% | Success rate |
| 7 | Connection Failure | TCP resets — timeouts and consecutive failures spike | Timeout rate, consecutive failures |
| 8 | Rate Limiting | HTTP 429 responses mixed with normal traffic | Success rate |
| 9 | Network Partition | Endpoint unreachable — all attempts time out | Timeout rate |
| 10 | Slow Degradation | Success rate decreases linearly 100% → 85% → 60% → 35% → 0% | All sub-metrics (gradual) |

### 4.6.1 Scenario Design Rationale

The scenarios are designed to exercise different aspects of the AEDE policy framework:

- **Scenarios 1, 8**: Test response to intermittent, non-catastrophic failures. Strategy A wastes retries uniformly; Strategy C should maintain AVAILABLE or OBSERVED_DEGRADATION without over-reacting.
- **Scenario 2**: Tests latency-only degradation. Success rate remains 100%, but latency climbs to 4500ms. Strategy C should detect degradation through the latency sub-metric even though no deliveries fail.
- **Scenarios 3, 4, 7, 9**: Test response to sudden, complete failures. Strategy A retries blindly throughout. Strategy C should quarantine and stop wasting retries within one ERI window.
- **Scenario 5**: Tests the hysteresis mechanism. An endpoint fluctuating around recovery thresholds should not cause rapid state oscillation.
- **Scenario 6**: Tests response to authentication failures (HTTP 401). The failure is deterministic and persistent — retrying will never succeed until credentials are rotated. Strategy C should quarantine; Strategy A retries 10 times per event regardless.
- **Scenario 10**: The strongest demonstration of AEDE's advantage. Gradual degradation exercises the full state machine progression: AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED. Strategy A retries at a constant rate throughout the degradation. Strategy C progressively reduces concurrency, increases backoff, and eventually quarantines — cumulatively wasting far fewer retries.

### 4.6.2 Scenario Implementation

Each scenario is implemented as a frozen `Scenario` dataclass containing an ordered list of `ScenarioPhase` objects:

```python
@dataclass(frozen=True)
class ScenarioPhase:
    duration_events: int     # how many telemetry events this phase lasts
    success_rate: float      # fraction that succeed (0.0–1.0)
    latency_ms: float        # average response latency
    timed_out_rate: float    # fraction that time out
    http_5xx_rate: float     # fraction returning HTTP 5xx
    description: str         # human-readable phase label
```

For example, Scenario 10 (Slow Degradation) defines six phases:

| Phase | Duration | Success Rate | Latency | Description |
|---|---:|---:|---:|---|
| 1 | 40 events | 100% | 100ms | Nominal baseline |
| 2 | 40 events | 85% | 350ms | Minor degradation |
| 3 | 40 events | 60% | 800ms | Moderate degradation |
| 4 | 40 events | 35% | 2000ms | Severe instability |
| 5 | 40 events | 0% | 4500ms | Complete failure |
| 6 | 60 events | 100% | 100ms | Full recovery |

---

## 4.7 Benchmark Runner Architecture

The Benchmark Runner is an in-process orchestrator that drives the Control Plane pipeline (ERI → State Machine → Policy Synthesiser) with synthetic telemetry derived from scenario definitions.

### 4.7.1 Synthetic Telemetry Generation

A `SyntheticTelemetryGenerator` produces deterministic `Telemetry` objects matching the phase specification. For each event, the generator:

1. Samples a uniform random variable $r \sim U(0, 1)$ using a seeded PRNG.
2. If $r < \text{success\_rate}$: the attempt succeeds with HTTP 200 and Gaussian-perturbed latency.
3. If $r \geq \text{success\_rate}$: the attempt fails. A second random variable determines the failure mode (timeout, HTTP 5xx, or other error) according to the phase's `timed_out_rate` and `http_5xx_rate`.

The seed is recorded in the `BenchmarkResult`, ensuring identical sequences across runs with the same seed.

### 4.7.2 Execution Loop

For each telemetry event generated from a scenario:

1. Append to the sliding window (last $N$ items).
2. Compute ERI via `compute_eri()`.
3. Evaluate the state machine via `next_state()` (Strategy C only; Strategies A and B always use AVAILABLE).
4. Obtain a `DeliveryPolicy` from the active strategy.
5. Record an `AttemptRecord` with retry count, endpoint state, and ERI score.
6. Update the failure streak counter.

The retry count for each strategy is derived from the actual policy:

| Strategy | Retry Count on Failure |
|---|---|
| A | `policy.retry_policy.max_retries` (always 10) |
| B | `policy.retry_policy.max_retries` (always 10, but with scaled delay) |
| C | `policy.retry_policy.max_retries` (varies by state: 10 → 8 → 5 → **0** → 6) |

The critical difference: when Strategy C quarantines an endpoint, `max_retries = 0`. Zero retries are wasted against a known-unavailable endpoint. Strategies A and B continue retrying at full rate.

### 4.7.3 Result Storage

After a run completes, the runner stores a `BenchmarkRunSummary` containing scenario, strategy, total events, success/failure counts, average retry count, and recovery time. This summary is immediately available to the dashboard's Chart Data API for visualisation.

---

## 4.8 Experimental Controls

### 4.8.1 Controlled Variables

The following variables are held constant across all three strategy comparisons:

- ERI weights (identical configuration for all strategies)
- State machine thresholds (identical for all strategies; Strategy A and B do not use the state machine, but the thresholds are available)
- Event volume and publication rate (determined by the scenario definition)
- Synthetic telemetry seed (identical random sequences for A, B, and C on the same scenario)
- Sliding window size ($N = 100$ attempts)
- Endpoint simulator configuration (same failure profiles)

### 4.8.2 Independent Variable

The sole independent variable is the delivery strategy: A (static), B (adaptive without state), or C (AEDE with full state model and policy synthesis).

### 4.8.3 Reproducibility

Every experimental run records:

- Platform version and policy version
- Complete configuration (thresholds, weights, flags)
- Random seed for telemetry generation
- Event dataset and volume
- Scenario definition (frozen dataclass — immutable)
- Timestamp

Results are independently reproducible: the same seed and scenario definition produce identical telemetry, and AEDE's determinism guarantee ensures identical policy decisions.

---

## 4.9 Statistical Analysis Plan

Multiple runs per experiment with different random seeds. Reported metrics include:

- Mean, median, and standard deviation
- 95% confidence intervals
- Statistical significance tests (paired t-test or Wilcoxon signed-rank test) to validate observed improvements where appropriate

The primary comparison is Strategy C vs Strategy A on Metric 2 (Average Retry Count) under Scenario 10 (Slow Degradation), as this scenario exercises the full state machine progression and most directly demonstrates the value of endpoint state-driven policy synthesis.

---

## 4.10 Threats to Validity

### 4.10.1 Internal Validity

**Threshold and weight sensitivity.** AEDE's policy decisions depend on configured ERI weights and state machine thresholds. Different configurations may produce different relative performance between strategies. *Mitigation*: all weights and thresholds are disclosed and held constant across strategy comparisons. A secondary weight sensitivity analysis (3–4 alternative configurations) is planned to demonstrate that the framework's advantage holds across reasonable weight variations, even if absolute performance differs.

**Single-endpoint evaluation.** MVP experiments evaluate AEDE against a single endpoint. Production systems deliver to many concurrent endpoints, where worker contention, shared queue depth, and cross-endpoint policy interactions may affect results. *Acknowledgement*: multi-endpoint evaluation is noted as future work. The single-endpoint design isolates the policy engine's behaviour from scheduling effects, which is appropriate for validating the framework's core mechanism.

### 4.10.2 External Validity

**Synthetic failure patterns.** The Endpoint Simulator produces controlled, parameterised failures. Real-world endpoints exhibit correlated failures, ambiguous partial responses (e.g., HTTP 200 with error body), deployment-window behaviour, and rate-limit patterns that may not match the simulator's modes. *Mitigation*: the simulator's failure modes cover the most common failure signatures observed in production webhook systems. The ten evaluation scenarios are designed to be representative, not exhaustive.

**Controlled event volume.** Experiments use a fixed event publication rate. Production systems experience bursty, non-uniform event volumes that interact with queue depth and ERI computation. *Acknowledgement*: variable-rate event publication is a future evaluation dimension.

### 4.10.3 Construct Validity

**ERI as a proxy for endpoint health.** The ERI is a weighted composite of observable metrics, not a direct measurement of endpoint availability. The composite score may mask important signals (e.g., a high success rate hiding increasing latency) or overweight unimportant ones. *Mitigation*: the ERI sub-scores are preserved in every `ExplainabilityRecord`, allowing post-hoc analysis of whether the composite score accurately reflected the endpoint's actual condition during each experiment.

**Window size sensitivity.** The attempt-count-based ERI window (default: 100 attempts) produces different responsiveness characteristics for high-volume vs low-volume endpoints. *Mitigation*: benchmark experiments use controlled, uniform event publication rates, eliminating this asymmetry during evaluation.

### 4.10.4 Reproducibility

All configurations, event datasets, policy parameters, simulator settings, and scenario definitions are published alongside the source code. Experiments use attempt-count-based ERI windows with explicit timestamp parameters (no wall-clock dependency inside AEDE), ensuring identical replay across runs.

---

## 4.11 Expected Outcomes

The proposed AEDE (Strategy C) is expected to:

1. **Reduce unnecessary retry attempts** — by quarantining degraded endpoints rather than retrying blindly.
2. **Reduce queue growth** — by suspending delivery to quarantined endpoints, preventing retry queue accumulation.
3. **Improve recovery stability** — by using hysteresis and gradual traffic restoration during recovery.
4. **Reduce operator intervention** — by automating isolation decisions that otherwise require manual action.
5. **Increase delivery efficiency** — by concentrating retry capacity on endpoints with a reasonable probability of success.
6. **Maintain 100% explainability coverage** — every automated decision produces a complete `ExplainabilityRecord`.
7. **Preserve deterministic behaviour** — identical inputs always produce identical policies.

These claims must be validated experimentally, not assumed.

---

## 4.12 What This Work Is Not Claiming

- **The ERI weights are not claimed to be optimal.** The weights are a documented design parameter — a reasonable starting configuration. Optimal weight discovery would require a separate experiment with access to ground-truth failure labels, which is out of scope.
- **AEDE is not a machine learning system.** No model is trained. No parameters are learned from data. The engine is deterministic and rule-based by design (ADR 0004).
- **This work does not propose a new retry algorithm.** Strategy B (adaptive backoff) is the closest existing approach. AEDE's contribution is the layer above the retry interval — the state model and policy framework that determines *when and whether* to retry at all.

---

## 4.13 Primary Research Contribution

The primary contribution is the **Adaptive Event Delivery Policy Framework**: a unified runtime system that couples continuous endpoint-state evaluation to runtime delivery policy synthesis in the specific constraints of outbound webhook delivery.

The experimental claim is:

> **Having a state model + policy engine (Strategy C) produces measurably better delivery outcomes than adaptive backoff without state (Strategy B), which in turn outperforms static retry (Strategy A), under realistic partial failure conditions.**

This is a systems/architecture contribution. The ERI weights and state machine thresholds are hyperparameters of the implementation, disclosed and held constant during the comparison experiments. The object of study is whether the framework architecture improves delivery outcomes — not whether any particular weight configuration is optimal.
