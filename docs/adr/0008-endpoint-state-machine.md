# ADR 0008: Five-State Endpoint State Machine with Hysteresis

## Status

Accepted

## Context

The platform must model the operational health of webhook endpoints. The state model chosen determines:
- How quickly the platform reacts to endpoint degradation
- Whether the platform oscillates between states under noisy conditions
- How delivery policy changes are triggered
- How recovery is handled after an outage
- What information is available to operators

Several approaches exist:

**Option A — Binary model (healthy / unhealthy)**: The endpoint is either available or not. Simple to implement, but loses nuance. A slowly degrading endpoint appears healthy until it suddenly becomes unhealthy. Recovery is also binary — the endpoint either works or doesn't. No graduated response.

**Option B — Continuous score only (no state machine)**: Delivery policy changes continuously as the ERI score changes. Smooth but difficult to reason about. An ERI of 67 has no clear meaning. State transitions don't produce observable events. Operators cannot tell "when did this endpoint become unstable?"

**Option C — Multi-state machine without hysteresis**: Multiple named states corresponding to distinct delivery behaviors. Transitions triggered when the ERI crosses a threshold. Clean and observable — but vulnerable to oscillation when the ERI hovers near a threshold.

**Option D — Multi-state machine with hysteresis**: Multiple named states with different thresholds for entering vs. leaving a state. Prevents oscillation. State changes correspond to observable operational events (EndpointStateChanged). Each state maps to a distinct delivery policy.

A binary model (Option A) is insufficient for the research contribution. The platform must demonstrate that graduated endpoint state drives graduated delivery policy. A continuous score only (Option B) produces unintelligible state from an operational perspective. Option C produces oscillation under realistic noisy conditions.

## Decision

Implement a **five-state endpoint state machine with hysteresis** (Option D).

### States

```
AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED → RECOVERING → AVAILABLE
```

**AVAILABLE** — Endpoint operating normally. High success rate, low latency.
- Policy: immediate delivery, normal concurrency, standard monitoring frequency.

**OBSERVED_DEGRADATION** — Early warning state. Increasing latency, isolated failures, elevated timeout frequency. Endpoint remains operational.
- Policy: increased monitoring frequency, moderate retry backoff, reduced parallel delivery.
- Rationale: Catching degradation early allows the platform to reduce load on a struggling endpoint before it fails completely.

**UNSTABLE** — Persistent instability. Repeated failures, poor success rate, sustained latency increase.
- Policy: aggressive backoff, suspend parallel deliveries, queue future events, notify administrator.
- Rationale: Continuing normal delivery against an unstable endpoint amplifies the subscriber's outage and fills the retry queue unnecessarily.

**QUARANTINED** — Delivery intentionally suspended. Endpoint considered unavailable.
- Policy: stop all delivery, preserve all queued events, await recovery evidence, permit manual replay.
- Rationale: Continuing retries against a quarantined endpoint wastes resources and may violate the subscriber's rate limits or amplify their outage.

**RECOVERING** — Endpoint has shown evidence of recovery but stability is not yet confirmed.
- Policy: gradual traffic restoration (single-stream delivery), conservative retry intervals, intensive monitoring.
- Rationale: Returning immediately to full concurrency after a quarantine period may overwhelm an endpoint that has just recovered. Gradual restoration confirms stability before resuming normal operation.

### Hysteresis

Simple threshold-based transitions produce oscillation when the ERI hovers near a boundary:

```
ERI: 79 → 81 → 79 → 81  (oscillates between AVAILABLE and OBSERVED_DEGRADATION)
```

Hysteresis prevents this by using different thresholds for degradation and recovery:

| Transition | Threshold |
|---|---|
| AVAILABLE → OBSERVED_DEGRADATION | ERI < 80 |
| OBSERVED_DEGRADATION → AVAILABLE (recovery) | ERI > 90 |
| OBSERVED_DEGRADATION → UNSTABLE | ERI < 60 |
| UNSTABLE → OBSERVED_DEGRADATION (recovery) | ERI > 70 |
| UNSTABLE → QUARANTINED | ERI < 30 for sustained period |
| QUARANTINED → RECOVERING | Manual trigger or scheduled probe success |
| RECOVERING → AVAILABLE | ERI > 90 sustained over recovery window |

The recovery threshold is always higher than the degradation threshold for the same transition. This means the platform degrades quickly (trust failure fast) and recovers cautiously (trust recovery slowly).

### Non-Adjacent Transitions

The linear diagram shows the typical degradation progression. Real endpoint behavior is not monotonic — an endpoint can collapse suddenly, recover partially, or require manual override. The state machine must define explicit rules for non-adjacent transitions to avoid getting stuck in unreachable states.

Explicit non-adjacent transitions:

- **OBSERVED_DEGRADATION → QUARANTINED**: Triggered when ERI collapses below the quarantine threshold within a single observation window. Handles sudden complete outages without requiring the endpoint to pass through UNSTABLE accumulation first.
- **QUARANTINED → AVAILABLE**: Manual admin force-clear (emergency override). Logged as an administrative action. Used when the operator has confirmed recovery through out-of-band means.
- **RECOVERING → QUARANTINED**: If ERI collapses again during gradual traffic restoration, the endpoint immediately re-quarantines. Prevents oscillating partial-recovery from causing a delivery storm.
- **ANY STATE → QUARANTINED**: Administrators may manually quarantine any endpoint regardless of current ERI. Logged as an administrative action.

All transitions — including non-adjacent ones — emit an `EndpointStateChanged` event with the originating state, destination state, triggering ERI, and triggering condition (threshold breach or administrative action).

### Transition Requirements

State transitions require:
1. **Evidence-based**: No single failure triggers a transition. Transitions require sustained evidence accumulated across the ERI observation window.
2. **Deterministic**: Identical ERI observations always produce identical transitions.
3. **Observable**: Every transition emits an `EndpointStateChanged` internal event with the previous state, new state, triggering ERI, and reason.

## Consequences

**Positive:**
- Five distinct states map to five distinct delivery policy profiles. The connection between endpoint health and delivery behavior is explicit and testable.
- Hysteresis eliminates oscillation under noisy conditions, providing stability without sacrificing responsiveness.
- Named states are observable and explainable. Operators see "endpoint is UNSTABLE" rather than "ERI = 62.4."
- State change events provide a complete audit trail. The EndpointStateChanged history shows exactly when degradation began and when recovery occurred.
- The state machine is independently testable: inject ERI values, assert state transitions, verify policies selected.
- The research contribution is concrete: AEDE's state-driven delivery policy can be compared against Strategy A (no state model) and Strategy B (ERI-driven backoff without states).

**Negative:**
- Five states require five distinct policy configurations. Threshold tuning is more involved than a binary healthy/unhealthy model.
- Operators must understand the state model to diagnose delivery issues effectively.
- Hysteresis thresholds must be tuned through experimentation — the default values are starting points.

**Accepted trade-offs:**
- The complexity of five states is justified by the research contribution. The state machine is the core mechanism that distinguishes AEDE from a simple adaptive backoff algorithm.
- Threshold tuning is addressed by the configuration philosophy (all thresholds externalized) and the benchmark framework (experiments systematically evaluate different threshold configurations).

The five-state model with hysteresis is the correct design because it provides the graduated, explainable, oscillation-resistant state management that the AEDE research contribution requires.
