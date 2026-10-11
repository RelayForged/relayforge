# ADR 0020: Operator Restore Resets Recovery Evidence

## Status

Proposed (implemented on `feature/demo-backend`; needs Person B sign-off because it touches the research core)

**Amends:** ADR 0012

## Context

ADR 0012 makes `QUARANTINED` a sink state that only an explicit action can leave, by setting the endpoint to `RECOVERING`. `AEDEService.restore_endpoint()` implemented that as a state flip and nothing else.

On the compose stack that is not enough. Reproduced on 2026-10-12 with the Full Outage and Recovery scenarios: after the restore message was applied the endpoint returned to `QUARANTINED` within one second and stayed there, with 196 deliveries parked in `PENDING` and no further attempts. Three things caused it:

1. **Stale window.** The sliding window still held the outage. The first observation after restore scored ERI 26, below the quarantine threshold, so `next_state(RECOVERING, …)` re-quarantined at once.
2. **Stale in-flight attempts.** Attempts that started before the restore finished after it (timeouts, scheduled retries) and were counted as recovery evidence.
3. **Stale policy.** `get_current_policy()` returns the cached policy when one exists, and the Dispatcher re-caches whatever it is given, so the cached QUARANTINE policy kept its TTL alive and HTTP stayed off.

Restore also published nothing, so the dashboard could not show `RECOVERING` until some later attempt happened to be evaluated.

## Decision

Operator restore on the live path (`AEDEService.restore_and_publish(endpoint_id, restored_at)`) does the following when the endpoint is `QUARANTINED`, and nothing otherwise:

1. Sets the state to `RECOVERING`.
2. Clears the endpoint's ERI sliding window. Recovery is judged on evidence gathered after the restore, which is the reading of "a *new* failure spike" in ADR 0012.
3. Records `restored_at`. Telemetry for an attempt that started before it (`observed_at − latency_ms < restored_at`) is excluded from the window. The Worker still persists that telemetry; only the recovery evaluation ignores it.
4. Synthesizes the `RECOVERING` policy and replaces the cached policy with it.
5. Saves an Explainability Record with the reason "Admin restore" and publishes a policy evaluation event.

`restored_at` is passed in by the Dispatcher. AEDE does not read the clock.

`AEDEService.restore_endpoint(endpoint_id)` keeps its old state-only behavior. The benchmark runner models restore as a state flip that keeps the window, and `benchmark/validate_against_real_aede.py` checks parity against that method.

## Consequences

- Restore works on the real stack: the same scenario now goes `QUARANTINED → RECOVERING → AVAILABLE`, and a failure spike after restore still re-quarantines.
- **The live restore path and the benchmark model now differ.** The benchmark keeps the pre-restore window; the platform does not. Published benchmark numbers are unchanged, but they describe the state-only model. Aligning the benchmark runner with this ADR would change those numbers and is a separate decision.
- Immediately after restore the ERI is reported as 100 with `window_size = 0`, because an empty window scores as perfect. The Explainability Record states that the window was reset.
- `RECOVERING` is short. With a healthy endpoint the first successful probe scores above the recovery threshold and the endpoint returns to `AVAILABLE` (about 3 seconds in the reproduction). If a visible probation period is wanted, the state machine needs a minimum sample count before promotion out of `RECOVERING`. That is not part of this decision.
- Deliveries parked during quarantine are not re-dispatched by restore. That is replay's job (ADR 0021).
