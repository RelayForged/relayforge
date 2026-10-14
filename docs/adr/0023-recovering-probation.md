# ADR 0023: Minimum Samples Before Leaving RECOVERING

## Status

Accepted (Person B, 2026-10-14)

**Amends:** ADR 0020

## Context

ADR 0020 clears an endpoint's ERI window on operator restore, so recovery is judged only on evidence gathered after the restore. An empty window scores as perfect, and the rule out of `RECOVERING` is only `ERI > recovery_threshold`. The first successful probe therefore promoted the endpoint to `AVAILABLE`. On the compose stack `RECOVERING` lasted about 3 seconds, and one good response was enough to declare an endpoint healthy again.

## Decision

`StateMachineThresholds` gains `recovery_min_samples` (default 0). In `next_state()`, `RECOVERING → AVAILABLE` also requires `eri.window_size >= recovery_min_samples`. `window_size` is the number of observations in the window.

- The re-quarantine path is unchanged. A bad score in `RECOVERING` still returns the endpoint to `QUARANTINED` straight away, whatever the sample count.
- `AEDEService` reads `AEDE_RECOVERY_MIN_SAMPLES` once, at construction, in the same way as `AEDE_QUARANTINE_THRESHOLD`. `next_state()` stays a pure function of its inputs.
- Compose sets 25, which is about 10 seconds of `RECOVERING` at the demo's event rate.

## Consequences

- Recovery on the live platform rests on 25 post-restore observations instead of one, and `RECOVERING` is visible on the dashboard.
- **The benchmark is unchanged.** Its thresholds leave `recovery_min_samples` at 0. Scenario 10 (seed 42) still gives wasted retries of 930 / 626 / 428 for A / B / C, and the parity check in `benchmark/validate_against_real_aede.py` passes.
- The live model now differs from the benchmark in two ways: ADR 0020 clears the window on restore, and this ADR adds probation. Both apply only after an operator restore. Evaluating them in the benchmark would change published numbers and is a separate decision.
- During probation the ERI reads 100, because every post-restore observation succeeded. The dashboard shows `RECOVERING` with a full score. That is expected: the state, not the score, carries the probation.
