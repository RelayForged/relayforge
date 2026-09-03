# Decision: Audit recommendations deliberately not implemented

- **Context:** The Week 3–4 technical audit produced recommendations that conflict with an established ADR, with the documented Strategy A contract, or with Person A's file ownership. Logged so they are not silently re-raised, and so the ones that need Person B are visible.

- **Declined 1 — "Fix the off-by-one in `max_retries`":** the audit read `attempt_number >= max_retries` with `max_retries=10` as yielding 9 retries rather than 10. Left as-is.
  - **Why:** This is the documented contract, not a defect. `retry-manager.md` Decision 1 states "Retry while `attempt_number < max_retries` (10 attempts total)", and the Week 3–4 integration test asserts it. The audit itself classified this as a semantics ambiguity rather than a bug. Changing the comparison would silently alter the total attempt count the sprint spec fixes at 10.

- **Declined 2 — "Add jitter to the retry delay":** no jitter added; the delay stays `min(initial_delay_seconds * backoff_multiplier ** (n - 1), max_delay_seconds)`.
  - **Why:** ADR 0003 and ADR 0005 confine the Retry Manager to executing a policy the Control Plane already produced, and `retry-manager.md` Decision 1 forbids re-deriving retry behaviour in the data plane. Jitter is not a field on `RetryPolicy`, so introducing it would mean the data plane inventing retry timing — a Control Plane decision. It would also break the required Strategy A sequence (5s, 10s, 20s, … cap 300s) that the integration test verifies.
  - **The thundering-herd concern is real.** The correct fix is a `jitter_ratio` field on the frozen kernel `RetryPolicy`, set by the strategy. **Flagged to Person B.**

- **Declined 3 — "Fix the `InMemoryPolicyCache` TTL bug":** `relay/infrastructure/cache.py` left untouched.
  - **Why:** File ownership. `cache.py` is read-only for Person A — Person B owns it for the Week 5–6 three-tier `PolicyCache` work, and it already has B's tests in `tests/unit/test_infrastructure.py`. Redis wiring is explicitly out of scope this sprint.
  - **The bug is real and worth relaying. Flagged to Person B:** `PolicyFallbackManager.get_tier_2_cached_policy` calls `cache.get_policy(endpoint_id)` without `current_time`, and `get_policy` only evaluates expiry when that argument is truthy — so Tier 2 serves stale policies forever and the 300s TTL never fires. Separately, `set_policy` stores a naive `datetime.now()`, so passing a timezone-aware `current_time` raises `TypeError: can't subtract offset-naive and offset-aware datetimes`. The cache dict is also unbounded, one entry per endpoint, never evicted.

- **Declined 4 — Any change to `feature_flags.py` or `explainability_repo.py`:** both read-only for Person A. The Dispatcher now *reads* `FeatureFlags.from_env()` and warns on a mismatch (see `dispatcher-concurrency-and-terminal-states.md` Decision 9) without modifying the module.

- **Declined 5 — Any change to `simulator/`:** Person C's. The `slow`-vs-`timeout` overlap the audit found is worked around with `WEBHOOK_TIMEOUT_SECONDS` instead (see `env-and-packaging.md` Decision 4).

- **Declined 6 — "Add the missing `__init__.py` files": the finding was incorrect.** All 32 markers already exist and are tracked; the audit's file search skipped zero-byte files. See `env-and-packaging.md` Decision 3.

## Deferred, not declined

- **`throughput_limit` (`ConcurrencyPolicy`)** is still unexecuted. `max_parallel` is now honored; a per-endpoint rate limit needs a token bucket with a time source, which is a larger change than this pass.
- **Time-batched offset commits.** ADR 0015's watermark already makes commits monotonic and suppresses redundant ones, so the remaining win is amortising the broker round trip. Per-completion commits were kept because they are precise about what is durable.
- **DNS-pinned egress.** ADR 0017's resolve-then-connect check is still TOCTOU. Closing it needs a custom `httpx` transport that connects to the vetted address.
- **Alerting on a stalled partition.** ADR 0015 deliberately withholds the offset on repeated failure, which is correct for at-least-once but means a permanently failing target stalls its partition's committed offset with nothing watching. `KafkaConsumer.pending_offsets()` exposes the signal; no component consumes it.
- **Per-endpoint HMAC secrets.** ADR 0016 uses one process-wide key because `Endpoint` has no secret field. **Needs Person B** if HMAC is ever switched on.
- **Owner scoping on `GET /events/{event_id}`.** ADR 0018 authenticates but does not authorize; `Event` carries no owner field. **Needs Person B.**
