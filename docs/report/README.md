# Relay — Academic / Technical Report

This directory contains the draft chapters of the final academic report for the Relay Event Delivery Platform.

## Report Structure & Chapter Ownership

| Chapter | Title | Primary Owner | Status | Source Documents |
|---|---|---|---|---|
| **01** | Introduction & Problem Statement | Team (A / B / C) | Planned | `docs/research.md` §Problem Statement |
| **02** | Related Work & Literature Gap | Team (A / B / C) | Planned | `docs/research.md` §Research Context & §Research Gap |
| **03** | System Design & Architecture | **B (Sam)** | ✅ **Drafted** (`03-system-design.md`) | `docs/architecture.md`, ADRs 0001–0013, implementation |
| **04** | Experimental Evaluation Methodology | **B (Sam)** | ✅ **Drafted** (`04-evaluation-methodology.md`) | `docs/research.md` §Methodology, `benchmark/` framework |
| **05** | Benchmark Evaluation Results | **B (Sam)** | Pending benchmark runs | Quantitative results from Strategy A vs B vs C comparison |
| **06** | Discussion & Threats to Validity | **B (Sam)** / Team | Drafted in Ch 4 | Weight sensitivity, external validity, threats |
| **07** | Conclusion & Future Work | Team (A / B / C) | Planned | `docs/research.md` §Future Work (Offline RL, etc.) |

---

## Chapter Summaries (Person B Deliverables)

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
- **10 Time-Sequenced Failure Scenarios** (Intermittent Timeouts, Latency, Burst 5xx, Outage, Partial Recovery, Auth 401, Reset, Rate Limit 429, Partition, Slow Degradation)
- **Benchmark Runner Architecture & Synthetic Telemetry Generator**
- **Controlled Variables & Reproducibility Guarantees**
- **Statistical Analysis Plan & Threats to Validity** (Internal, External, Construct)
- **Primary Research Contribution Framing** (Disclosed starting weights vs framework architecture)
