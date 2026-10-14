# ADR 0026: A Retry Chain Stopped by Quarantine Is Dead-Lettered

## Status

Accepted (Person B, 2026-10-14)

**Amends:** ADR 0021

## Context

ADR 0021 defines the DLQ as deliveries that are `FAILED`, `DEAD_LETTER`, or *parked*: `PENDING` with no attempt row, which is what the Dispatcher leaves when quarantine blocks the first attempt.

There was a fourth case. When a retry finished and the endpoint was by then quarantined, the Retry Manager scheduled nothing and left the Delivery `PENDING`. That delivery has attempt rows, so it is not parked. No retry is coming, and it is neither `FAILED` nor `DEAD_LETTER`. Nothing would ever deliver it, and replay could not find it.

Found in Demo 2 on 2026-10-14. A merchant's store was stopped while payments came in, and after restore and replay one of 11 payments was still `PENDING` with replay reporting nothing to do.

## Decision

When the Retry Manager does not schedule a retry because the policy is `QUARANTINE`, it marks the Delivery `DEAD_LETTER` and logs why.

The DLQ query already includes `DEAD_LETTER`. Replay then creates a new Event for it, with the original's channels, as for any failed delivery, and the original rows stay as they are.

## Consequences

- Every delivery that stops during a quarantine is now reachable: first attempts as parked, interrupted retry chains as `DEAD_LETTER`. After restore, `replay_dead_letters(endpoint_id=...)` covers both.
- `DEAD_LETTER` now has two causes: a missing or inactive endpoint, and a quarantine that ended a retry chain. The log line says which.
- A receiver may see a payment twice if a retry already in flight succeeds and the replay also delivers. Receivers must de-duplicate on a business key (the Demo 2 store uses the payment id), which webhook receivers must do anyway under at-least-once delivery.
- Deliveries stranded in `PENDING` before this change are not migrated.
