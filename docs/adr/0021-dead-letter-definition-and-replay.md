# ADR 0021: Dead-Letter Definition and Replay Through the Dispatcher

## Status

Proposed (implemented on `feature/demo-backend`)

## Context

The platform promises that replay from the DLQ is always possible and never mutates history. Two facts in the code stood in the way.

**What "dead letter" means was narrower than what gets stuck.**

- A delivery that exhausts its retries is marked `FAILED` by the Retry Manager, not `DEAD_LETTER`.
- `DEAD_LETTER` is only set when the endpoint is missing or inactive.
- Under a PAUSE or QUARANTINE policy the Dispatcher persists the Delivery as `PENDING`, writes no attempt, and commits the Kafka offset. Nothing dispatches it again. On the compose stack one outage left 621 deliveries in this state.

**Republishing the event does nothing.** A Delivery is unique on `(event_id, subscription_id)`. Sending the original event to `events.business` again reaches a delivery that is already terminal, and attempt 1 is already claimed, so the Dispatcher returns early. It would also fan out to every other subscriber of that event type.

## Decision

### Dead-letter set

A delivery is a dead letter when it is:

- `FAILED` or `DEAD_LETTER` and has not been replayed, or
- **parked**: `PENDING` with no attempt row and older than a short grace period.

`DeliveryRepository.get_dead_letters()` is the single definition. The dashboard's `GET /api/chart/dlq` and the Replay Manager both use it.

### Replay

1. `POST /replay` calls the Replay Manager (`relay/data_plane/replay/manager.py`), which selects dead letters and publishes one `ReplayWork` per delivery to a new topic, `events.internal.replay`.
2. The Dispatcher consumes it and calls its normal `_dispatch_one` for that single subscription. The policy therefore still comes from the Control Plane. The offset is committed only after the work is durable.
3. What is dispatched depends on why the delivery was stuck:
   - **Parked:** the original Event. Its Delivery already exists and attempt 1 is unclaimed.
   - **Failed / dead-lettered:** a new Event with the same type, payload and correlation id, whose `causation_id` is the original event id. It gets a new Delivery and attempt 1. The original Event, Delivery and attempts are not modified.
4. The replay Event's id is derived from the source delivery id (`replay_event_id`). Requesting the same replay twice, or a redelivered Kafka message, produces the same Event, and the existing idempotent writes absorb it.
5. A failed delivery counts as replayed once an Event caused by it has a Delivery for the same subscription. If that replay fails too, the replay's own Delivery is what appears in the dead-letter set.

### Gating

Operator replay is gated by the endpoint's isolation action only. If the endpoint is still paused or quarantined, the replayed delivery is parked again and no HTTP is sent. `replay_policy.allowed` is not consulted: it is `True` only in `RECOVERING`, and an operator must be able to replay once an endpoint is back to `AVAILABLE`. That flag is reserved for automatic replay, which does not exist yet.

## Consequences

- History is append-only under replay. Verified on the compose stack: the source delivery and attempt rows were byte-identical before and after.
- A replayed failure is traceable: correlation id is preserved and causation points at the original event.
- Replay does not duplicate deliveries to subscribers that already succeeded.
- The Replay Manager makes no policy decision and the Dispatcher makes none either; the Control Plane / Data Plane boundary holds.
- One more platform topic. Existing clusters get it from `ensure_topics()` at the next process start.
- Replay is manual. Deliveries parked during quarantine wait for an operator even after the endpoint recovers. Automatic drain on recovery would be the natural use of `replay_policy` and is left for later.
- `limit` on the request bounds a batch (default 50, the configured `replay_batch_size`). The Control Plane's per-state `replay_policy.batch_size` and `speed` are not applied.
