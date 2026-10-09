# Chapter 7 — Conclusion & Future Work

## 7.1 Summary of Findings

Chapters 4 and 5 tested the research hypothesis (§1.3) under a leakage-free calibration/held-out split, 14
failure scenarios, 5-seed statistical replication, and a weight sensitivity analysis — and, where the
first attempt at that evaluation turned out to be wrong, corrected it in the open rather than
discarding the record. The result that survived that process:

- On the held-out evaluation set — five genuine outage and severe-failure scenarios never seen during
  weight calibration — Strategy C (AEDE) reduces wasted retries by **86.9%** relative to static retry
  and **77.1%** relative to adaptive backoff without a state model, both significant at $p < 0.001$
  across 5 seeds (§5.4).
- State-machine transitions during adversarial flapping conditions dropped from an early, uncalibrated
  13.2 to **5.0**, completing a full quarantine-then-recovery cycle rather than oscillating (§5.5).
- All 28 weight-sensitivity perturbations remained stable: zero false quarantines, bounded transitions,
  outage reduction never below 81.8% (§6.2) — the result is not the product of one fragile
  configuration.
- A benchmark-harness bug (a shared random-number generator whose output depended on which other
  scenarios ran before a given one) was found, fixed, and every affected statistic in this report was
  regenerated under the corrected harness — including this chapter's headline figures. Fixing it also
  broke a specific claim that had been used to justify the deployed ERI weights: a fresh run of the
  same 3-seed genetic-algorithm search that originally selected them no longer converges to the same
  point. The weights were kept — they still pass every held-out acceptance gate under the corrected
  harness — but the "three seeds independently agree" evidence for them is retracted and replaced with
  the weaker, true claim: the search finds a *family* of structurally similar solutions, of which the
  deployed configuration is one validated member (§5.2).
- The **component ablation study (§5.8)** — added specifically to test whether the "unified
  framework" claim was substantive or just a restatement of "a system with a stop switch beats one
  without" — found a more precise and more defensible result than the aggregate number alone suggests:
  isolation drives almost all of the retry-reduction outcome; state-awareness without the ability to act
  on it barely helps; hysteresis's real contribution is a 3.5× reduction in state churn at statistically
  no cost to retry efficiency. The framework's actual contribution is the combination — efficiency from
  isolation, stability from hysteresis — not either piece alone.

## 7.2 What Was Not Demonstrated

Stated explicitly, because a conclusion that only lists successes is not trustworthy:

- **Global weight optimality was not established and is not claimed.** The deployed ERI weights are the
  output of a disclosed, reproducible, offline calibration process (§4.9), re-verified against the
  held-out set before adoption — not a hand-picked guess, but also not a claim that no better
  configuration exists.
- **The evaluation is substantially synthetic and single-endpoint.** The statistical results in §5.1 and
  §5.2–§5.11 come from an in-process benchmark harness calling the same control-plane functions the real
  system uses, not from the full Kafka/Dispatcher/Worker path under load. One real Kafka integration
  test and one multi-endpoint contention benchmark exist (§5.9–§5.10), but neither carries
  statistical weight in the headline claim.
- **"Wasted retries" is a closed-form proxy**, not a simulated retry loop with independent per-attempt
  outcomes (§6.4) — a deliberate, disclosed simplification, not an oversight.
- **Recovery speed was not part of the original acceptance gate**, and its absence let a real,
  previously-unvalidated default (`recovery_threshold = 90`) ship without anyone checking whether
  endpoints could actually finish recovering under it — two calibration scenarios could not. This has
  since been fixed and the gate closed (§6.4, §5.6), but it is recorded here as a reminder that
  an evaluation protocol is only as complete as the metrics someone thought to gate on.
- **AEDE is not a machine learning system**, and this work does not propose a new retry algorithm.
  Strategy B (adaptive backoff) is the closest prior approach; AEDE's contribution is the layer above
  the retry interval — the state model and policy framework deciding *whether* to retry at all, not a
  novel backoff formula.

## 7.3 Contributions, Restated Precisely

1. A state-driven endpoint health model derived entirely from externally observable signals (§2.4,
   Chapter 3 §3.4) — no cooperation from, or instrumentation of, the destination endpoint is assumed.
2. A policy framework coordinating retry, isolation, and hysteresis through one engine, with the
   specific contribution of *coordination* — as opposed to any one mechanism alone — isolated and
   evidenced directly (§5.8), rather than inferred from the aggregate result.
3. A complete, reproducible, open-source implementation, independently re-verifiable: every statistic in
   Chapters 4–6 was regenerated from the committed code as part of this work's own review process, more
   than once, after real discrepancies were found and fixed.
4. A reusable controlled fault-injection framework — 14 scenarios, calibration/held-out partitioning, an
   automated multi-metric acceptance gate (false quarantine, flapping, outage reduction, recovery
   completeness and speed) — available for future research into adaptive event delivery policies beyond
   this work.

## 7.4 Future Work

**Offline reinforcement learning for weight discovery.** The current ERI weights are discovered by an
offline genetic-algorithm search over a fixed, synthetic calibration scenario suite. A natural
extension replaces that fixed suite with real operational telemetry as the training signal: production
telemetry and delivery outcomes accumulate as a byproduct of normal operation (the `ExplainabilityRecord`
and `DeliveryAttempt` entities already capture what is needed), an offline batch process explores
alternative weight configurations against that historical data, candidates are validated against the
existing benchmark suite before promotion, and an operator approves the result before it reaches
production. This preserves AEDE's determinism guarantee fully: the RL agent never runs during policy
evaluation, only offline, and its output is a frozen configuration artifact deployed the same way a
manual `config.yaml` change would be. Online RL — adjusting weights during live delivery — was
considered and rejected: it requires deliberately degrading some live deliveries to explore the weight
space, weakens the determinism guarantee from global to epoch-local, and introduces a real-time reward
design problem that is safer to solve offline first.

**Closing the synthetic-vs-real gap.** The clearest concrete next step surfaced by this work's own
audit process: validate the benchmark harness's ERI/state-machine computations against the real,
in-process `AEDEService` on at least one held-out scenario, and — separately, and requiring platform
rather than research-track work — wire the live dashboard's read API to genuinely live delivery data
across the `dispatcher`/`api` process boundary, which currently only a unit test populates.

**Recovery-speed-aware calibration as a template.** The MTTR investigation in §5.6 fixed one
specific, previously-unvalidated default. The more general lesson — that an acceptance gate is only as
good as the metrics someone thought to include in it — suggests future calibration work should
routinely audit *which* operationally-relevant metrics are absent from the automated gate, not only
tune the ones already present.

**Multi-endpoint evaluation at scale.** The current multi-endpoint result (§5.9) covers two tenants
and a small worker pool. Evaluating noisy-neighbour isolation and cross-endpoint policy interaction at
production-representative scale — many concurrent endpoints, realistic traffic skew — remains open.

## 7.5 Closing Statement

The research hypothesis in §1.3 asked whether a state-model-driven policy engine can outperform
conventional static retry under realistic partial failure conditions. On the evidence in Chapters 4–6, the
answer is yes, on the metrics tested, within the scope disclosed in §7.2 — and the process of arriving
at that answer, including the errors found and corrected along the way, is itself part of what is being
published: the complete implementation, every benchmark script, and every number in this report are
independently reproducible from the committed source, not asserted from it.
