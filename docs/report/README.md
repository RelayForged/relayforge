# Relay — Academic / Technical Report

This directory contains the draft chapters of the final academic report for the Relay Event Delivery Platform.

## Report Structure & Chapter Ownership

| Chapter | Title | Primary Owner | Status | Source Documents |
|---|---|---|---|---|
| **01** | Introduction & Problem Statement | Team (A / B / C) | ✅ **Drafted** (`01-introduction.md`) | `docs/research.md` §Problem Statement |
| **02** | Related Work & Literature Gap | Team (A / B / C) | ✅ **Drafted** (`02-related-work.md`) | `docs/research.md` §Research Context & §Research Gap |
| **03** | System Design & Architecture | **B (Sam)** | ✅ **Drafted** (`03-system-design.md`) | `docs/architecture.md`, ADRs 0001–0013, implementation |
| **04** | Evaluation Methodology | **B (Sam)** | ✅ **Drafted** (`04-evaluation-methodology.md`) | `docs/research.md` §Methodology, `benchmark/` framework |
| **05** | Benchmark Evaluation Results | **B (Sam)** | ✅ **Drafted** (`05-benchmark-results.md`) | Quantitative results from Strategy A vs B vs C comparison |
| **06** | Discussion & Threats to Validity | **B (Sam)** / Team | ✅ **Drafted** (`06-discussion-threats-to-validity.md`) | Weight sensitivity, external validity, threats |
| **07** | Conclusion & Future Work | Team (A / B / C) | ✅ **Drafted** (`07-conclusion.md`) | `docs/research.md` §Future Work (Offline RL, etc.) |

Chapters 5 and 6 were split out from what was originally a single, combined Chapter 4 — results,
discussion, and threats to validity now sit as their own chapters rather than being folded into the
methodology chapter. This was a restructuring of existing content (section relocation and
renumbering, all cross-references updated throughout the report), not new material: every number,
table, and finding is unchanged from the combined draft.

---

## Chapter Summaries

### [Chapter 1: Introduction](01-introduction.md)
- Motivation: webhook delivery as an infrastructure problem, not an application-level retry loop
- The core problem, reframed as a distributed-systems question rather than an HTTP one
- Research question and hypothesis
- AEDE overview (control-plane policy engine, deterministic by design, not a learning system)
- Contributions overview and report roadmap

### [Chapter 2: Related Work & Literature Gap](02-related-work.md)
- Five intersecting research domains (distributed systems, EDA, stream processing, cloud-native infra,
  developer experience)
- Literature position across five prior-work categories (adaptive retry/circuit breaking, endpoint
  health detection, messaging/stream processing, cloud security, commercial developer tooling)
- The compound gap: unified, externally-observable-only, experimentally-validated, openly-published —
  stated as a literature gap, not a claim that no comparable system has ever been built
- Four constraints explaining why service-mesh adaptive controllers cannot be directly repointed at
  webhook delivery, and how each motivates a specific AEDE design choice

### [Chapter 3: System Design](03-system-design.md)
Comprehensive technical specification of Relay and the Adaptive Event Delivery Engine (AEDE):
- **Architectural Principles & 5-Layer Stack** (Developer Interfaces, Control Plane, Kafka, Data Plane, Infrastructure)
- **Control Plane / Data Plane Inviolable Separation**
- **Adaptive Event Delivery Engine (AEDE)** (Philosophy, Determinism guarantee)
- **Endpoint Reliability Index (ERI)** (Multi-signal mathematical formulation, weights, attempt-count windowing, sub-metric snapshots)
- **Endpoint State Machine** (5 states, hysteresis equations, non-adjacent transition matrix, quarantine terminality)
- **Policy Synthesizer** (Mapping state + ERI to immutable `DeliveryPolicy` objects)
- **Three-Tier Policy Fallback Hierarchy** (AEDE Live → Redis 5-min TTL → Tier 3 Static Defaults)
- **Explainability Engine** (`ExplainabilityRecord`, metric preservation, human-readable audit trail)
- **Feature Flags for Research Switching** (`STATIC_RETRY_MODE`, `ADAPTIVE_RETRY_MODE`, `AEDE_ENABLED`)
- **Domain Model & 11-Stage Event Lifecycle**

### [Chapter 4: Evaluation Methodology](04-evaluation-methodology.md)
Formal experimental evaluation design:
- **Research Question & Hypothesis ($H_1$)**
- **Constraints on Webhook Delivery** (External ownership, weak observability, lack of traffic control, external failure amplification)
- **Strategy Formulations**:
  - Strategy A: Static Exponential Backoff Baseline
  - Strategy B: Adaptive Backoff Baseline without State Model
  - Strategy C: Full AEDE Policy Engine
- **10 Research Metrics** (Delivery Success Rate, Average Retry Count, MTTR, etc.)
- **14 Time-Sequenced Failure Scenarios** (the original 10-scenario taxonomy, plus a calibration-only
  scenario and three held-out scenarios added to close specific coverage gaps found during evaluation:
  re-quarantine relapse, adversarial flapping, and long-duration mild degradation)
- **Benchmark Runner Architecture & Synthetic Telemetry Generator**
- **Controlled Variables & Reproducibility Guarantees**
- **Leakage-Free Calibration / Held-Out Evaluation Split**
- **Hyperparameter Calibration and Determinism** — the two-stage (hand-picked sweep, then
  genetic-algorithm search) ERI weight calibration process and the hysteresis-widening and
  `recovery_threshold` fixes, including a documented instance of the calibration/held-out split
  catching and fixing a real overfitting risk
- **Primary Research Contribution Framing** (Disclosed, reproducible weight calibration vs framework
  architecture as the actual object of study)
- **Expected Outcomes**, validated experimentally in Chapter 5

### [Chapter 5: Benchmark Evaluation Results](05-benchmark-results.md)
The empirical evidence for the research hypothesis:
- **Statistical Analysis & Hypothesis Testing** — paired t-tests, whole-suite and held-out comparisons
- **Weight Optimization Results** (Stage 1 hand-picked sweep, Stage 2 genetic-algorithm search)
- **Multi-Seed Overall Benchmark Comparison** across all 14 scenarios
- **Hypothesis 1–3 Validation** (outage retry reduction, hysteresis stability, quarantine isolation &
  autonomous MTTR), each on the leakage-free held-out set, including the documented MTTR
  investigation-and-correction narrative
- **Detailed Per-Scenario Breakdown** across all 14 scenarios
- **Component Ablation Study** (§5.8) isolating the individual contribution of isolation vs.
  hysteresis, rather than asserting the combined framework is beneficial from the aggregate result alone
- **Multi-Endpoint Contention**, **Kafka Chain Integration**, and **Full-Stack Live E2E Demo** results

### [Chapter 6: Discussion & Threats to Validity](06-discussion-threats-to-validity.md)
- **Discussion** synthesising Chapter 5's findings against the research hypothesis, including the
  qualified reading of the headline retry-reduction number that the ablation study supports
- **Internal Validity**: empirical weight sensitivity analysis (28 perturbations) and multi-endpoint
  contention
- **External Validity**: synthetic failure patterns, controlled event volume
- **Construct Validity**: ERI as a health proxy, window-size sensitivity, the wasted-retries modeling
  assumption, and the recovery-speed acceptance-gate gap found and closed during this work
- **Reproducibility**: configuration, seeds, and scenario definitions published alongside source code

### [Chapter 7: Conclusion & Future Work](07-conclusion.md)
- Summary of findings, restated precisely against what Chapters 4–6 actually evidenced
- Explicit statement of what was **not** demonstrated (synthetic/single-endpoint evaluation, retry-count
  proxy metric, weight calibration scope) rather than only what was
- Contributions restated against their supporting evidence
- Future work: offline RL for weight discovery, closing the synthetic-vs-real validation gap,
  recovery-speed-aware calibration as a general template, multi-endpoint evaluation at scale
