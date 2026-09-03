# ADR 0015: Commit Kafka Offsets by Per-Partition Watermark, Not Per Message

**Status:** Accepted

**Context**

ADR 0002 grounds at-least-once in one mechanism: "Recovery from worker failure is straightforward: re-deliver from the last committed Kafka offset." That guarantee only holds if an offset is committed strictly after its work is durable.

The Week 3–4 Dispatcher violated this on the retry path. `handle_retry` committed the offset the moment `RetryWork` parsed, then held the pending retry in an `asyncio.create_task` sleeping up to `max_delay_seconds` (300s). The offset was gone while the only record of the scheduled work was process memory:

- A crash or restart during the backoff permanently lost the retry. The `delivery_attempts` row existed with `completed_at IS NULL`, but nothing re-derives due work from Postgres, so the Delivery was stranded in `PENDING` forever.
- `run()`'s `finally` closed the consumer without awaiting scheduled tasks, so even a clean shutdown dropped them.

That is at-most-once for retries, which contradicts ADR 0002 outright.

A second, subtler problem blocked the fix. `commit(message)` committed `message.offset + 1` directly. That is only correct while messages are processed strictly one at a time. The moment the Dispatcher processes concurrently — required to stop one slow endpoint from stalling a whole partition — committing offset 6 while offset 3 is still in flight acknowledges offset 3 as done. A crash then skips it.

Kafka has no delayed-topic API, so the backoff wait must live somewhere. Keeping it on the Dispatcher (never the Worker or Retry Manager, per ADR 0003/0005 and the sprint rules) remains the right call; what changes is when the offset is acknowledged.

**Decision**

1. **The consumer wrapper tracks in-flight offsets per `TopicPartition`.** `messages()` records an offset as in-flight when it is yielded. `commit(message)` moves it to completed and then commits a *watermark*: the lowest in-flight offset, or `max(completed) + 1` when nothing is in flight. An incomplete offset blocks every offset behind it.

2. **A message is acknowledged only after its work is durable.** For `events.business`, after all fan-out targets have persisted. For `events.internal.delivery`, after the backoff has elapsed *and* the attempt has executed and persisted — not on parse.

3. **Shutdown cancels in-flight work without committing.** `_drain()` cancels both message and retry tasks and logs the uncommitted offsets. Waiting out a 300s backoff would stall shutdown; acknowledging it would drop a retry. Redelivery is the correct third option.

4. **A processing failure withholds the commit.** An exception during fan-out is logged and the offset is left uncommitted. Only an unparseable payload is committed-and-skipped, because it can never succeed.

**Consequences**

Positive:
- Retries survive crash and restart. The failure mode is a duplicate delivery, which ADR 0002 explicitly accepts, rather than a silently dropped event, which it explicitly rejects.
- The Dispatcher can process messages concurrently without loss, which is what makes bounded parallel fan-out safe.
- Redelivery is genuinely idempotent: `Delivery.save` no-ops on `(event_id, subscription_id)`, `insert_if_absent` no-ops on `(delivery_id, attempt_number)`, Telemetry no-ops on `attempt_id`, and `_dispatch_one` stands down when the Delivery is already terminal or attempt 1 already completed.

Negative:
- One stuck message stalls its partition's committed offset. In-flight offsets accumulate in memory and, on restart, work already done is re-processed. This is deliberate: stalling is observable and recoverable, loss is not. It does mean a permanently failing target needs alerting, which no component owns yet.
- On restart, a retry mid-backoff re-executes from the beginning of its wait, so the effective delay can exceed the policy delay. Delays are a floor, not a schedule.
- Partition revocation during a rebalance leaves stale in-flight state. Uncommitted offsets are redelivered to the new owner, so this is safe, but the tracker does not yet clear itself on revocation.

**Supersedes**

Decision 3 of `docs/small-decisions-personA/retry-manager.md` ("Dispatcher commits the retry offset, then `create_task` waits"). The wait still happens on the Dispatcher; the commit moved to after execution.
