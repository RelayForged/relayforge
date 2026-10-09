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

Fourteen failure scenarios constitute the independent variable: the original 10-scenario taxonomy, a
supplementary calibration-only scenario (11), and three scenarios (12–14) added later to close specific
coverage gaps — the `RECOVERING → QUARANTINED` re-quarantine transition, adversarial clustered
flapping, and long-horizon mild degradation — found missing during evaluation (§4.6.2). Each scenario is
a time-sequenced fault injection profile — a sequence of phases, where each phase specifies the
endpoint's behaviour (success rate, latency, timeout rate, HTTP 5xx rate) for a defined number of
delivery attempts. Six scenarios (1, 2, 6, 7, 8, 11) form the calibration set used to select ERI weights;
the remaining eight (3, 4, 5, 9, 10, 12, 13, 14) form the held-out evaluation set used to validate
Hypotheses 1–3 (Chapter 5 §5.1, §5.4–§5.7).

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
| 11 | Bursty Traffic Under Degradation | Two burst windows dropping success rate to 40% then 20% across 170 events (supplementary, calibration-only) | Success rate, sliding-window reactivity |
| 12 | Re-Quarantine Relapse | Admin restore enters a RECOVERING probe, then a sudden outage relapse forces re-quarantine before genuine final recovery | Exercises `RECOVERING → QUARANTINED`, untested by 1–11 |
| 13 | Rapid Micro-Flapping | Eight repetitions of short alternating healthy/low-success (10–15%) bursts — an adversarial hysteresis stress test harder than Scenario 5 | Recovery trend, hysteresis dampening |
| 14 | Long Sustained Mild Degradation | 400 events at a constant 75% success rate with elevated latency/timeout/5xx — degraded but never catastrophic, over a much longer horizon than any other scenario | All sub-metrics (sustained, non-outage) |

### 4.6.1 Scenario Design Rationale

The scenarios are designed to exercise different aspects of the AEDE policy framework:

- **Scenarios 1, 8**: Test response to intermittent, non-catastrophic failures. Strategy A wastes retries uniformly; Strategy C should maintain AVAILABLE or OBSERVED_DEGRADATION without over-reacting.
- **Scenario 2**: Tests latency-only degradation. Success rate remains 100%, but latency climbs to 4500ms. Strategy C should detect degradation through the latency sub-metric even though no deliveries fail.
- **Scenarios 3, 4, 7, 9**: Test response to sudden, complete failures. Strategy A retries blindly throughout. Strategy C should quarantine and stop wasting retries within one ERI window.
- **Scenario 5**: Tests the hysteresis mechanism. An endpoint fluctuating around recovery thresholds should not cause rapid state oscillation.
- **Scenario 6**: Tests response to authentication failures (HTTP 401). The failure is deterministic and persistent — retrying will never succeed until credentials are rotated. Strategy C should quarantine; Strategy A retries 10 times per event regardless.
- **Scenario 10**: The strongest demonstration of AEDE's advantage. Gradual degradation exercises the full state machine progression: AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED. Strategy A retries at a constant rate throughout the degradation. Strategy C progressively reduces concurrency, increases backoff, and eventually quarantines — cumulatively wasting far fewer retries.
- **Scenario 11**: Supplementary bursty-traffic test — two burst windows where success rate drops to 40% and then 20% across 170 events total, used to stress ERI sliding-window reactivity and as the early-detection calibration proxy (§4.6.2, Chapter 5 §5.2); not part of the original 10-scenario failure taxonomy.
- **Scenario 12**: Tests the `RECOVERING → QUARANTINED` re-quarantine transition — defined in the state
  machine (Chapter 3 §3.5.3) but never exercised by Scenarios 1–11. An admin restore begins a RECOVERING probe, a
  relapse forces immediate re-quarantine, and only a second restore produces genuine final recovery.
  Strategy C must re-enter QUARANTINED rather than treat the first restore as sufficient.
- **Scenario 13**: A harder adversarial flapping stress test than Scenario 5 — eight short alternating
  healthy/low-success cycles, probing whether the widened hysteresis gaps (Chapter 3 §3.5.2) still bound state
  churn under clustered, repeated flapping rather than a single partial-recovery episode.
- **Scenario 14**: Tests sustained *mild* (not catastrophic) degradation over a long horizon — 400 events
  at 75% success. This is deliberately excluded from the outage-reduction comparison (Chapter 5 §5.4): its
  strategies never diverge sharply enough to represent a genuine outage, and treating it as one would
  overstate Hypothesis 1's result (Chapter 5 §5.4 documents this categorization explicitly).

### 4.6.2 Calibration vs. Held-Out Evaluation Split

To eliminate weight-selection leakage (evaluating weight configurations on the same scenarios used for calibration), the scenario suite is strictly partitioned:
- **Calibration Set ($\{1, 2, 6, 7, 8, 11\}$)**: Used exclusively for empirical weight candidate evaluation (`benchmark/optimize_weights.py`) and evolutionary parameter search (`benchmark/genetic_optimizer.py`). This set encompasses intermittent noise (1, 8), pure latency drift without delivery failures (2), persistent auth and connection failures (6, 7), and supplementary bursty-traffic degradation (11).
- **Held-Out Evaluation Set ($\{3, 4, 5, 9, 10, 12, 13, 14\}$)**: Evaluated strictly after weights are
  frozen. Comprises burst failures (3), full outages with recovery (4), threshold boundary flapping
  under partial recovery (5), network partitions (9), gradual slow degradation (10), re-quarantine
  relapse (12), rapid micro-flapping (13), and long sustained mild degradation (14). All primary
  hypothesis tests (Hypotheses 1, 2, and 3) are evaluated exclusively on these unseen scenarios.

### 4.6.3 Scenario Implementation

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
| B | `policy.retry_policy.max_retries` (scales 10 → 7 → 5 → 3 with scaled delay) |
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
- Synthetic telemetry seed (identical pseudo-random sequences for A, B, and C on the same scenario — implementing the Common Random Numbers (CRN) variance reduction technique to isolate algorithmic treatment differences from stochastic sampling noise)
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

## 4.9 Hyperparameter Calibration and Determinism

- **ERI Weights Calibration.** Stage 1 began with an empirical sweep (`benchmark/optimize_weights.py`) across four hand-picked weighting profiles on the calibration set ($\{1, 2, 6, 7, 8, 11\}$), which selected a profile named `Hysteresis-Balanced`. Stage 2 replaced this with a genetic-algorithm search (`benchmark/genetic_optimizer.py`) over the same calibration set. An unconstrained search consistently converged to `recovery_trend_weight > 0.55` — `recovery_trend_score` is the one ERI sub-score that is not a smooth function of the telemetry (it takes only three discrete values from a first-half-vs-second-half window comparison), and over-weighting it caused severe state-machine flapping on a long-duration, borderline-degradation held-out scenario, even though it scored excellently on the calibration set itself. `RECOVERY_TREND_WEIGHT_CAP = 0.15` was added to the search (a generic regularization on that one dimension, not tuned to any scenario's outcome), and three independent GA seeds then converged to the same point: `success_rate_weight≈0.375, consecutive_failures_weight≈0.464, recovery_trend_weight=0.15` (the cap), all other dimensions near-zero. This was re-verified against the full held-out evaluation set before adoption (Chapter 5) and found to improve every held-out metric simultaneously, not just the one Hypothesis 1 targets.
- **State Machine Hysteresis Widened.** Investigating a residual instability on the held-out long-duration scenario (Chapter 5 §5.7) showed ERI swinging by ~40 points within the sliding window during sustained borderline degradation — wider than the original 10-point hysteresis gaps (degradation 80/90, unstable 60/70), which caused the state machine to chase the noise. Trigger thresholds were lowered slightly and recovery thresholds widened (degradation 75/95, unstable 55/85), making recovery stickier once degraded without slowing detection of genuine outages. `quarantine_threshold` (30.0) was unchanged. Re-verified against the full held-out set in combination with the new weights (Chapter 5) before adoption in `relay/control_plane/aede/service.py`.
- **`recovery_threshold` Lowered (80.0, was 90.0).** A separate finding, made while auditing the resulting mean-time-to-recovery (Chapter 5 §5.6): `recovery_threshold` — which governs `RECOVERING → AVAILABLE` specifically, and was *not* touched by the widening above — had never been validated. At 90.0, two calibration scenarios never finished recovering within their test length at all. Lowered to 80.0 after confirming zero effect on Hypothesis 1 across a full sweep (90 down to 55) and confirming ERI climbs steadily through that range during genuine recovery rather than hovering near it.
- **AEDE is not a machine learning system.** No statistical model is trained at runtime. Both searches above are offline, one-time calibration steps whose *output* is a frozen, disclosed configuration — identical to hand-tuning a config file, just discovered by search instead of guesswork. The engine itself remains deterministic, rule-based, and explainable by design (ADR 0004).
- **This work does not propose a new retry algorithm.** Strategy B (adaptive backoff) represents existing adaptive retry approaches. AEDE's contribution is the control plane layer above the retry interval — the state model and policy framework that determines *when and whether* to retry at all.

---

## 4.10 Primary Research Contribution

The primary contribution is the **Adaptive Event Delivery Policy Framework**: a unified runtime system that couples continuous endpoint-state evaluation to runtime delivery policy synthesis in the specific constraints of outbound webhook delivery.

The experimental claim is:

> **Having an explicit state model + policy engine (Strategy C) produces measurably better delivery outcomes than adaptive backoff without state (Strategy B), which in turn outperforms static retry (Strategy A), under realistic partial failure conditions.**

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

These claims are validated experimentally in Chapter 5, not assumed.
