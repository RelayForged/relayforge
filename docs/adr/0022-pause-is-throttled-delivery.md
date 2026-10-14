# ADR 0022: PAUSE Is Throttled Delivery, Not a Stop

## Status

Accepted (Person B, 2026-10-14)

## Context

The synthesizer gives an `UNSTABLE` endpoint `IsolationAction.PAUSE`, with `max_parallel = 1`, a 15 s initial backoff and up to 5 retries (`relay/control_plane/aede/synthesizer.py`, and the state table in `docs/report/03-system-design.md`).

The Dispatcher and the Retry Manager treated `PAUSE` exactly like `QUARANTINE`: no HTTP, and the Delivery was left `PENDING`. Without HTTP there is no telemetry, so the ERI cannot change and `next_state()` never runs again for the endpoint. Operator restore only acts on `QUARANTINED` (ADR 0020). An endpoint that reached `UNSTABLE` therefore stayed there.

Reproduced on the compose stack on 2026-10-14. The simulator ran at a 50% HTTP 500 rate, then was set healthy. The endpoint reached `UNSTABLE` and its last attempts brought the ERI back to 79.2. That is below the 85 needed to step down to `OBSERVED_DEGRADATION`. After that, no attempt was made for the rest of the run, and 1,036 deliveries were left `PENDING`. The same deadlock had already been noticed and worked around in `tests/integration/test_kafka_pipeline.py`.

The benchmark never had this problem. `benchmark/runner.py` keeps delivering for Strategy C in `UNSTABLE`, using the policy's concurrency and backoff, and isolates only in `QUARANTINED`. The published results describe that behaviour.

## Decision

Only `QUARANTINE` stops HTTP. Under `PAUSE` the Dispatcher dispatches and the Retry Manager schedules retries as for any other policy, so the `UNSTABLE` policy's backoff and concurrency limit are what throttle the endpoint.

The synthesizer is unchanged. It still emits `PAUSE` for `UNSTABLE`. The `IsolationAction.PAUSE` docstring now describes it as throttled delivery.

## Consequences

- An `UNSTABLE` endpoint now recovers on its own, or degrades to `QUARANTINED`, from live telemetry. The live platform matches the benchmark model and the report's state table.
- `PAUSE` no longer creates parked deliveries. Parked deliveries come only from `QUARANTINE`. ADR 0021's DLQ definition still covers both, so earlier rows remain replayable.
- **`max_parallel = 1` is not yet enforced exactly.** The Dispatcher's per-endpoint semaphore is sized from the first policy it sees for an endpoint, usually `AVAILABLE` with 10. In `UNSTABLE` the long backoff spaces out retries, but first attempts of new events are not limited to one at a time. A resizable limiter is a separate change. A naive one would block the Dispatcher's consume loop, which also carries restore commands.
- The name `PAUSE` now overstates what happens. Renaming the enum value would change persisted policies and the report, so it is kept.
