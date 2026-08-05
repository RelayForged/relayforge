# ADR 0012: Endpoint State Machine Quarantine Terminality and Non-Adjacent Recovery Transitions

## Status

Accepted

## Context

The Endpoint State Machine (`relay/control_plane/state_machine/transitions.py`) models endpoints across 5 discrete operational states:
1. `AVAILABLE`
2. `OBSERVED_DEGRADATION`
3. `UNSTABLE`
4. `QUARANTINED`
5. `RECOVERING`

During system design and implementation of AEDE, a critical architectural decision arose regarding state transitions into and out of `QUARANTINED`:
1. Should an endpoint in `QUARANTINED` automatically self-recover to `RECOVERING` or `AVAILABLE` if calculated ERI score improves (e.g., if sliding window telemetry clears out old errors)?
2. How should non-adjacent transitions (such as sudden catastrophic outages or recovery failures) be handled by the transition function?

## Decision

We establish two explicit state machine laws in `next_state()`:

### 1. `QUARANTINED` is a Terminal Sink State in Automated State Evaluation
In `next_state(current_state, eri, thresholds)`:
- When `current_state == EndpointState.QUARANTINED`, the evaluation function **always returns `EndpointState.QUARANTINED`**, regardless of the composite ERI score.
- **Rationale**: When an endpoint is quarantined, active event deliveries are halted (`retry_policy.max_retries = 0`). Because no delivery attempts are being made, any apparent rise in calculated ERI score would be an artifact of sliding-window math, not evidence of actual endpoint health restoration.
- **Recovery Requirement**: Exiting `QUARANTINED` requires an explicit external action (setting `current_state = EndpointState.RECOVERING`), triggered either by:
  a) A scheduled synthetic probe success (probe manager).
  b) A manual administrator override action (Dashboard/Admin API).

### 2. Explicit Non-Adjacent Transitions and Hysteresis
The state transition grammar explicitly supports non-adjacent transitions to handle non-monotonic real-world failure patterns:

- **Catastrophic Drop (`AVAILABLE → QUARANTINED`)**: If an endpoint suffers a sudden 100% failure rate (e.g., HTTP 503 / timeouts across all attempts in a window), ERI drops below `quarantine_threshold` (30.0). The state machine immediately jumps from `AVAILABLE` to `QUARANTINED`, bypassing `OBSERVED_DEGRADATION` and `UNSTABLE`.
- **Re-Quarantine (`RECOVERING → QUARANTINED`)**: An endpoint in `RECOVERING` undergoes single-stream gradual traffic restoration. If a new failure spike occurs during recovery causing ERI to fall below `quarantine_threshold`, the endpoint is immediately re-quarantined (`RECOVERING → QUARANTINED`).
- **Hysteresis Anti-Flapping**: State recovery requires stronger evidence than state degradation:
  - Transition INTO `OBSERVED_DEGRADATION`: ERI < 80.0
  - Transition BACK to `AVAILABLE`: ERI > 90.0 (`degradation_recovery_threshold`)
  - Transition INTO `UNSTABLE`: ERI < 60.0
  - Transition BACK to `OBSERVED_DEGRADATION`: ERI > 70.0 (`unstable_recovery_threshold`)

## Consequences

- **Safety Guarantee**: Quarantined endpoints will never resume event delivery prematurely due to passive sliding-window telemetry expiry.
- **Clean Audit Trail**: Admin overrides and probe actions are recorded explicitly as state transition events with distinct trigger reasons.
- **Anti-Flapping**: Asymmetric hysteresis boundaries prevent state machine oscillation when an endpoint operates near degradation boundaries.
