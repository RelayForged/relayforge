# ADR 0013: Explainability Record Schema and Metric Preservation

## Status

Accepted

## Context

Principle 7 of the Relay Event Delivery Platform states:
> **Intelligence Must Be Explainable** — Every automated decision exposes why it was made.

AEDE automatically synthesizes `DeliveryPolicy` instances based on endpoint telemetry and ERI scores. In production systems, operators and developers need to understand *why* an endpoint was throttled, paused, or quarantined. Furthermore, for the research contribution, every experimental run comparing Strategy A, B, and C must produce structured, auditable proof of why each policy choice was executed.

To support this requirement, AEDE must generate an immutable `ExplainabilityRecord` alongside every `DeliveryPolicy`.

Two implementation challenges arose:
1. **Sub-score Preservation**: Should ERI sub-scores (success rate, latency score, timeout rate, consecutive failure penalty, etc.) be recomputed when generating explainability records?
2. **Determinism & Audit Integrity**: How do we ensure explainability records are deterministic and linkable to the exact policy evaluation?

## Decision

We establish the `ExplainabilityRecord` schema in `relay/control_plane/explainability/record.py` and enforce sub-score storage directly on `ERIScore`:

### 1. Direct Metric Preservation on `ERIScore`
`ERIScore` (`relay/kernel/value_objects/eri.py`) carries both the composite score (0.0–100.0) and all individual normalized sub-scores (0.0–1.0):
- `success_rate_score`
- `latency_score`
- `consecutive_failure_score`
- `timeout_score`
- `http_5xx_score`
- `queue_depth_score`
- `recovery_trend_score`

**Rationale**: Storing normalized sub-scores directly on the `ERIScore` value object eliminates the need to re-evaluate raw telemetry or duplicate weighting logic during explainability record generation, guaranteeing 100% consistency between the score computed and the explainability record stored.

### 2. Explainability Record Structure
Every policy evaluation via `synthesize_policy()` produces an immutable `ExplainabilityRecord` carrying:
- `record_id`: Unique identifier (UUID).
- `policy_id`: Unique identifier of the associated `DeliveryPolicy`.
- `endpoint_id`: Target endpoint ID.
- `state_before` & `state_after`: Operational state transition (`EndpointState` strings).
- `state_changed`: Boolean indicating if a transition occurred.
- `metrics`: `MetricsSnapshot` containing composite ERI and all 7 normalized sub-scores.
- `thresholds`: `ThresholdSnapshot` containing all state machine threshold values active during evaluation.
- `triggered_thresholds`: List of specific rule/threshold strings crossed (e.g. `"ERI (55.1) < unstable_threshold (60.0)"`).
- `policy_changes`: Human-readable summary of policy field adjustments made.
- `reason`: Comprehensive justification string for operators.
- `strategy_used`: Identifier of the strategy engine (`STRATEGY_A`, `STRATEGY_B`, `STRATEGY_C`).
- `evaluated_at`: Timestamp passed by the caller (no `datetime.now()` inside AEDE).
- `window_size`: Number of delivery attempts included in the evaluation window.

### 3. Persistence Repository
All records are saved via `ExplainabilityRepository` (`relay/infrastructure/persistence/explainability_repo.py`), enabling read-only querying by the Live Dashboard API and audit log services.

## Consequences

- **Complete Transparency**: Dashboard and audit logs can immediately display *which metric caused degradation* (e.g. latency spike vs HTTP 500 streak).
- **Deterministic Audit**: Passing `computed_at` explicitly ensures that re-evaluating historical telemetry produces identical explainability records.
- **Zero Recomputation**: Storing sub-scores on `ERIScore` avoids recalculating metric formulas when building explainability records.
