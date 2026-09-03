# Decision: Dispatcher bounded concurrency, terminal states, and policy execution

- **Context:** The Week 3–4 audit found the Dispatcher processed one Kafka message at a time with the outbound HTTP call inline, caught only `ValidationError`, never assigned `DEAD_LETTER`, and ignored four `DeliveryPolicy` sub-policies. Offset durability is covered separately by ADR 0015; this note covers the execution-shape decisions around it.

- **Decision 1 — Bounded concurrency with two independent semaphores:** `DISPATCHER_MAX_CONCURRENT_MESSAGES` (default 16) bounds business messages; `DISPATCHER_MAX_IN_FLIGHT_RETRIES` (default 500) bounds retries waiting out a backoff. Fan-out targets run under `asyncio.gather`.
  - **Why:** With a 5s client timeout and inline HTTP, a serial loop meant one slow endpoint stalled a whole partition for `5 x N` seconds. The two pools must be separate: a retry sleeping 300s must not hold a message slot, or 16 sleeping retries would halt ingestion entirely. Blocking on semaphore acquisition is deliberate backpressure onto polling. Safe only because ADR 0015 made concurrent commits correct.

- **Decision 2 — Fan-out failure re-raises; the offset is withheld:** `_fan_out` gathers with `return_exceptions=True` and re-raises the first failure. `handle_message` logs and returns without committing.
  - **Why:** ADR 0002 prefers a duplicate over a loss. Targets that already succeeded are no-ops on redelivery, guarded by Decision 4.

- **Decision 3 — Pool sized to the concurrency ceiling:** `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` default to 20/20 with `pool_recycle=1800`.
  - **Why:** SQLAlchemy's default 5+10 would have become the real concurrency limit, with tasks silently blocking on connection checkout.

- **Decision 4 — Stand down on already-resolved work:** `_dispatch_one` returns early if the loaded Delivery is `SUCCESS` / `FAILED` / `DEAD_LETTER`, and `_persist_attempt` returns `None` if attempt 1 already has `completed_at` set.
  - **Why:** Without these, a redelivered Kafka message would re-POST a delivery the retry chain already owns, or one already marked terminal. Idempotent `save()` protects the rows; these guards protect the *side effect*.

- **Decision 5 — No attempt row when isolation blocks HTTP:** on `PAUSE` / `QUARANTINE`, the Delivery is persisted `PENDING` and `_dispatch_one` returns *before* claiming attempt 1. Policy evaluation moved ahead of the attempt insert to make this possible.
  - **Why:** Previously attempt 1 was inserted first and then abandoned with `completed_at IS NULL`. That row occupied the only attempt-1 slot, so a later dispatch found it present, treated it as in flight, and the Delivery could never make progress. Persist-first is still satisfied: the Delivery row is durable, and the attempt row is written before any HTTP.

- **Decision 6 — `DEAD_LETTER` for undeliverable-by-configuration:** a missing or `is_active=False` endpoint closes out the attempt with `error_message="undeliverable: endpoint missing|inactive"` and sets the Delivery to `DEAD_LETTER`. Retry exhaustion and an expired deadline stay `FAILED`.
  - **Why:** The status existed in the kernel and in ADR 0014 but was never assigned. A missing endpoint is a configuration fact no retry can fix; the previous silent `return` stranded the Delivery in `PENDING` with an attempt that never completed. `FAILED` means "we tried and it kept failing", `DEAD_LETTER` means "we cannot try". The dashboard adapter passes `status` through as a plain string, so the new value needs no change there.

- **Decision 7 — `max_parallel` enforced per endpoint:** `_endpoint_slot()` keeps an `asyncio.Semaphore` per `endpoint_id`, sized from the first policy seen for that endpoint.
  - **Why:** ADR 0005 says the Worker executes the policy mechanically; `concurrency_policy.max_parallel` was simply ignored. Fixed at first sight because `asyncio.Semaphore` cannot be resized without dropping held slots — exact for Week 3–4, since Strategy A always yields 1. `throughput_limit` is still unimplemented; it needs a token bucket.

- **Decision 8 — Catch broadly in the consumer loop, and log:** `handle_message` catches `Exception` around fan-out; `_run_scheduled` catches around execution and re-raises `CancelledError` untouched; `main()` calls `logging.basicConfig` with `LOG_LEVEL`.
  - **Why:** Only `ValidationError` was handled, so a transient asyncpg error or a commit failure during rebalance propagated out of the `async for` and terminated the process. `CancelledError` must pass through so ADR 0015's shutdown drain works. Without `basicConfig` the existing `logger.warning` calls had no configured handler.

- **Decision 9 — Feature flags are read and warned about, not obeyed:** `_strategy` stays `StrategyA()`; a mismatch against `flags.active_strategy_name` logs a warning naming `STATIC_RETRY_MODE`.
  - **Why:** `self._flags` was assigned and never read. The sprint rule is Strategy A only, so honoring the flag is out of scope, but silently ignoring configuration is worse than announcing that it is being overridden. The warning fires today, since the default flag set resolves to `STRATEGY_C`.
