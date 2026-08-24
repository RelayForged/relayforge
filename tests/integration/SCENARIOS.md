# Control Plane Integration Test Scenarios

This document describes the integration test scenarios in
`test_control_plane_loop.py`. Each scenario exercises the full Control Plane
pipeline end-to-end:

```
Telemetry → compute_eri() → next_state() → synthesize_policy()
                                              ↓
                                        PolicyCache
                                              ↓
                                    ExplainabilityRepository
```

All scenarios use real `Telemetry` objects (not `ERIScore.perfect()`/`.zero()`
shortcuts) to drive the pipeline with realistic delivery attempt data.

Reference: `docs/architecture.md`

---

## Scenario 1: Full Degradation Cascade

**What it simulates**: An endpoint that progressively fails over time, walking
through every degradation state.

**State trajectory**: `AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED`

**How**:
- Start with 10 successful delivery attempts (ERI ≈ 100).
- Inject increasing failure rates: 30% failures → 60% failures → 100% failures.
- After each batch, recompute ERI and run state machine + policy synthesizer.

**Verifications**:
- ERI drops monotonically across batches.
- State machine transitions in order: AVAILABLE → DEGRADED → UNSTABLE → QUARANTINED.
- `isolation_policy.action` changes: CONTINUE → REDUCE_CONCURRENCY → PAUSE → QUARANTINE.
- `concurrency_policy.max_parallel` decreases at each step.
- `notification_policy.notify_admin` becomes True at UNSTABLE or QUARANTINED.
- An `ExplainabilityRecord` is produced at every evaluation.

**Architecture reference**: Lines 267-283 (State Definitions), 300-320
(Non-Adjacent Transitions)

---

## Scenario 2: Recovery Trajectory

**What it simulates**: An endpoint that has been manually moved to RECOVERING
state and gradually proves its reliability until restored to AVAILABLE.

**State trajectory**: `RECOVERING → RECOVERING → AVAILABLE`

**How**:
- Start in `RECOVERING` state with ERI = 85 (below recovery_threshold of 90).
- Inject 20 consecutive successes with good latency.
- Recompute ERI. ERI should rise above 90, triggering AVAILABLE.

**Verifications**:
- `replay_policy.allowed` is `True` while RECOVERING.
- `replay_policy.speed` is conservative (< 1.0) while RECOVERING.
- `concurrency_policy.max_parallel` is 1 (single-stream) while RECOVERING.
- Once ERI > 90, state transitions to AVAILABLE.
- Policy restores full concurrency and normal retry behavior.

**Architecture reference**: Lines 281-282 (RECOVERING definition), Line 315
(RECOVERING → AVAILABLE transition), Lines 291-298 (Hysteresis)

---

## Scenario 3: Quarantine is Terminal Without External Override

**What it simulates**: Once quarantined, an endpoint cannot self-recover through
ERI improvement alone. External intervention is required.

**State trajectory**: `QUARANTINED → QUARANTINED` (stuck)

**How**:
- Start in `QUARANTINED` state.
- Inject 20 perfect delivery attempts (simulating probe successes).
- Recompute ERI (should be 100).
- Run `next_state()` — should still return QUARANTINED.

**Verifications**:
- `next_state()` returns QUARANTINED regardless of ERI value.
- Policy keeps `isolation_policy.action = QUARANTINE`.
- `retry_policy.max_retries = 0` (no delivery attempts).
- This confirms quarantine exit requires an external state override to RECOVERING
  (scheduled probe success or manual admin action per arch.md line 313).

**Architecture reference**: Line 313 ("QUARANTINED → RECOVERING: Scheduled probe
succeeds OR manual admin trigger")

---

## Scenario 4: Hysteresis Prevents State Flapping

**What it simulates**: An endpoint whose ERI oscillates near the degradation
boundary. Without hysteresis, the state would flip-flop every evaluation cycle.

**State trajectory**: `AVAILABLE → OBSERVED_DEGRADATION → (stays DEGRADED despite
ERI rising above 80)`

**How**:
- Start AVAILABLE with ERI = 85.
- Drop ERI to 75 → transitions to OBSERVED_DEGRADATION.
- Raise ERI to 82 (above degradation_threshold of 80 but below
  degradation_recovery_threshold of 90) → should remain OBSERVED_DEGRADATION.
- Raise ERI to 85 → should remain OBSERVED_DEGRADATION.
- Raise ERI to 91 → should transition back to AVAILABLE.

**Verifications**:
- State does NOT return to AVAILABLE when ERI crosses 80 from below.
- State ONLY returns to AVAILABLE when ERI exceeds 90 (recovery threshold).
- This is the core hysteresis mechanism preventing oscillation.

**Architecture reference**: Lines 291-298 ("INTO degradation: ERI < 80, BACK to
healthy: ERI > 90")

---

## Scenario 5: Non-Adjacent Skip (Catastrophic Failure)

**What it simulates**: An endpoint that suffers a sudden, complete outage —
100% failure in a single evaluation window. The state machine should skip
directly from AVAILABLE to QUARANTINED without passing through intermediate
states.

**State trajectory**: `AVAILABLE → QUARANTINED` (single step)

**How**:
- Start AVAILABLE with ERI = 95.
- Inject 20 attempts: all failures (HTTP 500), all timed out, high latency.
- Recompute ERI (should be ≈ 0).
- Run state machine — should jump directly to QUARANTINED.

**Verifications**:
- State transitions directly from AVAILABLE to QUARANTINED.
- No intermediate OBSERVED_DEGRADATION or UNSTABLE state.
- ExplainabilityRecord captures the skip (state_before=AVAILABLE,
  state_after=QUARANTINED, state_changed=True).
- Triggered thresholds include quarantine_threshold crossing.

**Architecture reference**: Line 309 ("ERI collapses below quarantine threshold
in a single window — sudden complete failure bypasses UNSTABLE")

---

## Scenario 6: Three-Tier Policy Fallback Under AEDE Outage

**What it simulates**: The Dispatcher requesting policies when AEDE becomes
unavailable, testing the graceful degradation hierarchy.

**Flow**:
1. **Tier 1**: AEDE generates a live policy → cached in PolicyCache with 5-min TTL.
2. **Tier 2**: Simulate AEDE crash. Dispatcher fetches from cache → gets valid
   cached policy.
3. **Tier 3**: Advance clock by 301 seconds (TTL expired). Cache returns None →
   PolicyFallbackManager returns static fallback.

**Verifications**:
- Tier 1 policy matches AEDE output exactly.
- Tier 2 returns cached policy within TTL window.
- Tier 2 cache expires after 300 seconds (configurable via POLICY_CACHE_TTL_SECONDS).
- Tier 3 static fallback has: `initial_delay=5s`, `backoff_multiplier=2.0`,
  `max_delay=300s`, `max_retries=10`, `max_parallel=1`, `isolation=CONTINUE`,
  `notify_admin=True`.
- Tier 3 never quarantines (arch.md line 555: "never quarantine without AEDE
  confirmation").

**Architecture reference**: Lines 531-562 (Failure Model — Control Plane Failures)

---

## Scenario 7: Feature Flag Strategy Switching

**What it simulates**: The same endpoint telemetry evaluated under three different
feature flag configurations, producing three different but valid policies.

**How**:
- Generate a telemetry stream with 70% success rate.
- Evaluate with `STATIC_RETRY_MODE=True` → Strategy A.
- Evaluate with `ADAPTIVE_RETRY_MODE=True` → Strategy B.
- Evaluate with defaults (all enabled) → Strategy C (AEDE).

**Verifications**:
- Strategy A: fixed `initial_delay=5.0s`, `max_parallel=1`, same policy regardless
  of success rate.
- Strategy B: `initial_delay` scales inversely with success rate, `max_parallel`
  adjusts based on health.
- Strategy C: uses full state machine + ERI. Policy reflects endpoint state.
- All three produce valid `DeliveryPolicy` objects.
- All three produce `ExplainabilityRecord` objects with correct `strategy_used` field.
- Feature flags correctly select strategy via `active_strategy_name`.

**Architecture reference**: Lines 594-606 (Feature Flags for Research)

---

## Scenario 8: Determinism Guarantee

**What it simulates**: The fundamental AEDE requirement that identical inputs
always produce identical outputs.

**How**:
- Create a fixed telemetry stream (10 attempts with known outcomes).
- Run the full pipeline twice with identical inputs (same state, same ERI, same
  config, same `computed_at` timestamp).
- Compare outputs field-by-field.

**Verifications**:
- Both runs produce DeliveryPolicies with identical fields (except `policy_id`
  which is UUID-generated).
- Both runs produce ExplainabilityRecords with identical fields (except
  `record_id`).
- `eri_score`, `endpoint_state`, all sub-policy fields, `reason` string,
  `triggered_thresholds`, and `policy_changes` are all byte-identical.

**Architecture reference**: Lines 199-201 ("Given identical endpoint observations,
identical configuration, and identical policy rules, AEDE must always generate
identical Delivery Policies"), Line 288 ("Identical observations always produce
identical transitions")

---

## Scenario 9: Explainability Completeness

**What it simulates**: Verification that every policy evaluation — whether it
triggers a state change or not — produces a fully populated ExplainabilityRecord.

**How**:
- Run 5 evaluations: one per state (AVAILABLE, OBSERVED_DEGRADATION, UNSTABLE,
  QUARANTINED, RECOVERING).
- Check every ExplainabilityRecord field.

**Verifications**:
- `record_id` is non-empty and unique per evaluation.
- `policy_id` links to the corresponding DeliveryPolicy.
- `state_before` and `state_after` are valid EndpointState values.
- `metrics` snapshot has all 7 sub-scores populated (0.0-1.0) and composite_eri
  (0.0-100.0).
- `thresholds` snapshot has all 6 threshold values populated.
- `reason` string is non-empty and human-readable.
- `triggered_thresholds` is non-empty when a state change occurs.
- `policy_changes` is non-empty when a state change occurs.
- `strategy_used` is one of: STRATEGY_A, STRATEGY_B, STRATEGY_C.
- `evaluated_at` matches the caller-supplied timestamp (determinism).
- `window_size` matches the telemetry count.

**Architecture reference**: Lines 238-240 ("Every ERI evaluation exposes: computed
score, individual metric values..."), Line 386 ("Every automated decision is
explainable")

---

## Scenario 10: Re-Quarantine During Recovery

**What it simulates**: An endpoint that enters RECOVERING but suffers another
failure spike, causing immediate re-quarantine. This prevents premature recovery
from becoming an oscillation cycle.

**State trajectory**: `RECOVERING → QUARANTINED`

**How**:
- Start in `RECOVERING` state with moderate ERI (e.g., 70).
- Inject 10 failed delivery attempts (100% failure rate).
- Recompute ERI (should drop below quarantine_threshold of 30).
- Run state machine — should transition back to QUARANTINED.

**Verifications**:
- State transitions from RECOVERING to QUARANTINED.
- ExplainabilityRecord captures the re-quarantine reason.
- Policy reverts to full quarantine posture (max_retries=0, isolation=QUARANTINE).
- This is explicitly identified in arch.md as one of the two most important
  non-adjacent transitions.

**Architecture reference**: Line 316 ("RECOVERING → QUARANTINED: ERI collapses
again during recovery — re-quarantine"), Line 320 ("An endpoint that fails again
during gradual traffic restoration re-enters quarantine immediately")
