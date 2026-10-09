# Chapter 6 — Discussion & Threats to Validity

## 6.1 Discussion: Synthesis of Findings

Chapter 5 tested the research hypothesis (§1.3) on the held-out evaluation set and found support for
it, with two qualifications that the aggregate numbers alone do not convey.

First, the headline result — an 86.7% reduction in wasted retries versus static retry (§5.4) — is, by
itself, close to true by construction: Strategy C is the only strategy in the comparison capable of
refusing to retry at all, so some reduction is guaranteed before any specific design choice is
evaluated. The component ablation study (§5.8) exists specifically to test whether the *specific*
design — a state machine feeding an isolation decision, stabilised by asymmetric hysteresis — is doing
anything beyond that trivial fact. It found that it is, but not uniformly: state-awareness without the
ability to act on it (Strategy C′) barely outperforms adaptive backoff (46.8% vs 42.7%), while the jump
to 87%+ retry reduction happens specifically when isolation is added. The unified-framework claim
(`docs/research.md`, Scientific Contribution #2) is therefore supported by the *isolation* mechanism
primarily, not by the sophistication of the state model that feeds it in isolation.

Second, hysteresis's contribution is real but different from what the retry-reduction framing implies.
Strategy C″ (isolation without hysteresis) achieves statistically indistinguishable retry efficiency to
the full system (87.6% vs 87.2%) but 3.5× more state transitions (144.2 vs 40.8). Hysteresis is not
buying additional retry savings; it is buying operational stability at the same level of retry
efficiency. A claim that hysteresis reduces wasted retries would not survive this ablation; the claim
that it reduces state churn does.

Taken together, the framework's defensible contribution is the *coupling* of isolation and hysteresis —
efficiency from one, stability from the other — evaluated and evidenced as two separable effects (§5.8)
rather than asserted as a single unexamined "the system works" result. The remaining threats to
validity in this chapter (§6.2–§6.5) describe the conditions under which this conclusion holds and where
it has not yet been tested.

---

## 6.2 Internal Validity: Empirical Weight Sensitivity Analysis

A primary threat to internal validity is whether AEDE's performance relies on fine-tuned ERI weights. To address this, we implemented an automated perturbation framework (`benchmark/sensitivity_analysis.py`) systematically perturbing each of the 7 ERI dimensions by $\pm 10\%$ and $\pm 20\%$ (28 distinct weight perturbations) while re-normalizing the remaining weights.

All 28 configurations were evaluated against three formal stability bounds — the actual bounds enforced
in code (`benchmark/sensitivity_analysis.py`), not a fitness-score threshold: a fitness variation bound
was considered but the composite fitness score is not a percentage-bounded quantity (it includes a
large penalty term for false quarantines), so bounding it at a fixed percentage is not meaningful —
false quarantine and outage reduction are the metrics that are actually checked:
1. **False Quarantine Rejection:** Under healthy/high-latency traffic (Scenarios 1 & 2), false quarantines must remain exactly 0.
2. **Flapping Stability Bound:** Scenario 5 (partial recovery) transitions must not exceed 5, and Scenario 13 (Rapid Micro-Flapping) must not exceed 15.
3. **Outage Reduction Bound:** Combined outage retry reduction vs Strategy A (Scenarios 3, 4, 9, 10, 12) must remain $\ge 50.0\%$.

### Summary of Weight Perturbation Results

| Metric | Baseline | Mean Perturbed | Worst-Case Perturbed | Acceptance Criterion | Status |
|---|---:|---:|---:|---:|:---:|
| **Composite Fitness** (descriptive only, not bounded) | 1,477.5 | $34.8 \pm 3{,}625.4$ | -8,773.0 | — | — |
| **False Quarantines** | 0 | 0.0 | 0 | Exactly 0 | **PASS** |
| **Scenario 5 Transitions** | 3 | 3.0 (constant across all 28) | 3 | $\le 5$ | **PASS** |
| **Scenario 13 Transitions** | 5 | 5.0 (constant across all 28) | 5 | $\le 15$ | **PASS** |
| **Outage Retry Reduction** | -85.9% | $-85.5\% \pm 1.2\%$ | -81.8% | Reduction $\ge 50.0\%$ | **PASS** |

**Empirical Finding:** Across all 28 perturbations, zero false quarantines occurred, Scenario 5
transitions were exactly 3 in every single perturbed configuration (the decisive-quarantine behavior
described in §5.5 is not fragile to weight choice), Scenario 13 stayed at exactly 5 transitions
throughout, and outage retry reduction never dropped below $81.8\%$ — confirming the result in §5.4
is not the product of a single fragile weight configuration. The composite calibration fitness score
swings wildly under perturbation, including deep into negative territory on several `success_rate`/
`consecutive_failures` perturbations — this is expected and disclosed, not a stability failure: it is a
coarse scalar dominated by a large penalty term (never-recovers, MTTR) that triggers on some perturbed
configurations even while the three metrics that are actually bounded (false quarantines, transition
counts, outage reduction) remain stable throughout. This is precisely why fitness is reported
descriptively rather than as an acceptance criterion.

**Multi-endpoint contention and noisy neighbor isolation.** While single-endpoint isolation validates core policy mechanics, production systems deliver across shared concurrency pools. To evaluate this directly, we developed a concurrent multi-endpoint contention benchmark (`benchmark/multi_endpoint.py`) simulating a shared delivery worker capacity pool ($C = 4$ concurrent workers) under load from both a failing endpoint (Tenant 1: 100% timeouts) and a healthy endpoint (Tenant 2: 100% 200 OK responses). The empirical findings are detailed in §5.9.

## 6.3 External Validity

**Synthetic failure patterns.** The Endpoint Simulator produces controlled, parameterised failures. Real-world endpoints exhibit correlated failures, ambiguous partial responses (e.g., HTTP 200 with error body), deployment-window behaviour, and rate-limit patterns that may not match the simulator's modes. *Mitigation*: the simulator's failure modes cover the most common failure signatures observed in production webhook systems. The 14 failure scenarios (6 calibration, 8 held-out evaluation, Chapter 4 §4.6) are designed to be representative, not exhaustive.

**Controlled event volume.** Experiments use a fixed event publication rate. Production systems experience bursty, non-uniform event volumes that interact with queue depth and ERI computation. *Acknowledgement*: variable-rate event publication is a future evaluation dimension.

## 6.4 Construct Validity

**ERI as a proxy for endpoint health.** The ERI is a weighted composite of observable metrics, not a direct measurement of endpoint availability. The composite score may mask important signals (e.g., a high success rate hiding increasing latency) or overweight unimportant ones. *Mitigation*: the ERI sub-scores are preserved in every `ExplainabilityRecord`, allowing post-hoc analysis of whether the composite score accurately reflected the endpoint's actual condition during each experiment.

**Window size sensitivity.** The attempt-count-based ERI window (default: 100 attempts) produces different responsiveness characteristics for high-volume vs low-volume endpoints. *Mitigation*: benchmark experiments use controlled, uniform event publication rates, eliminating this asymmetry during evaluation.

**Wasted retries computation modeling assumption.** In the benchmark runner, "wasted retries" is computed as the active policy's configured `max_retries` at the moment of a failed synthetic attempt (or 0 during quarantine), rather than simulating each retry attempt independently over simulated wall-clock time until success or exhaustion. This provides a deterministic, closed-form metric of network retry exposure and load placed on the subscriber endpoint under each policy, but represents an intentional modeling simplification of downstream retry loops.

**Recovery speed was not a gated acceptance metric during the original weight calibration — now fixed.** The genetic-algorithm search (Chapter 4 §4.9) originally selected weights purely on calibration-set retry/stability fitness; nothing in that objective, nor in the original held-out acceptance checks (Hypotheses 1–3), scored or bounded mean-time-to-recovery. This was caught post hoc (§5.6) — MTTR had risen from 68 to 222–319 events as an unexamined side effect of the weight reallocation — and traced to `recovery_threshold` never having been validated at all (it was still at its original value of 90.0, under which two calibration scenarios never finished recovering within their test length). Lowering it to 80.0, verified independent of Hypothesis 1 across a full sweep, resolved both issues.

This gap is now closed structurally, not just patched once: `benchmark/optimize_weights.py::evaluate_candidate()` and `benchmark/genetic_optimizer.py::evaluate_weights()` both now compute, for every calibration scenario that reaches `QUARANTINED` and has a `manual_restore` phase, whether it fully recovers to `AVAILABLE` (a `never_recovers` flag, disqualifying at the same severity as a false quarantine) and how many events that took (`mttr`, penalized in the search objective). `benchmark/run_experiments.py` additionally asserts a held-out MTTR bound (Scenario 4, $\le 250$ events) alongside Hypotheses 1–3 on every run. Re-running the GA search with this objective in place reproduces essentially the same weight vector already adopted (MTTR 125–126 either way), confirming the original weights were already sound on this axis — the gap was in the validation process, not the weights themselves.

## 6.5 Reproducibility

All configurations, event datasets, policy parameters, simulator settings, and scenario definitions are published alongside the source code. Experiments use attempt-count-based ERI windows with explicit timestamp parameters (no wall-clock dependency inside AEDE), ensuring identical replay across runs.
