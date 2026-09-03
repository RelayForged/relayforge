# Data-Plane Test Suite

**Context**

Person A had no automated tests. Every test under `tests/` belonged to
Person B (`relay/kernel`, `relay/control_plane`) or Person C (`simulator/`,
`relay/dashboard`); nothing imported `relay/data_plane/`, the persistence
repositories, or the Kafka wrappers. `pytest` was not installed and was
not declared in `pyproject.toml` or `requirements.txt`.

Verification was entirely `scripts/week34_smoke.py` and
`scripts/api_smoke.sh`. Those cover the happy path end-to-end against
real Postgres and Kafka, but they cannot reach the cases that motivated
most of the audit fixes: concurrent out-of-order message completion, a
crash mid-batch, redelivery of an already-completed attempt, a DNS record
that changes after registration, or a policy that demands HMAC with no
secret configured. Sprint item 8 (Strategy A retry delays) also had no
test — the smoke confirms attempt 2 gets scheduled but asserts nothing
about the 5s/10s/…/300s sequence, the cap, or exhaustion.

**Decision**

Add `tests/data_plane/` covering the Person A surface, structured as:

| File | Covers |
|---|---|
| `test_retry_schedule.py` | Sprint item 8: Strategy A delay sequence, `max_delay_seconds` cap, 10-attempt budget, `deadline_seconds`, isolation holds, redelivery idempotency |
| `test_offset_commits.py` | ADR 0015 watermark commits |
| `test_url_guard.py` | ADR 0017 egress guard, including the dev bypass |
| `test_webhook_worker.py` | ADR 0016 request contract, HMAC, Simulator failure-mode mapping |
| `test_dispatcher_states.py` | Commit discipline, terminal states, dead-lettering, `max_parallel` |
| `test_api_security.py` | ADR 0018 authentication and ownership, request boundary limits |

Supporting choices:

1. **In-memory doubles, not a test database.** `_helpers.py` holds fakes
   that run with no Docker, so the suite is ~1s and needs no
   `docker compose up`. The fakes reproduce the *conflict semantics* of
   the real SQL — `ON CONFLICT (event_id, subscription_id) DO UPDATE …
   WHERE delivery_id = EXCLUDED.delivery_id` returning `None` on a no-op,
   `insert_if_absent` versus `save` targeting different constraints,
   suppressing a completion update once `completed_at` is set. A fake that
   simply inserted would let every test pass while at-least-once
   redelivery corrupted production data. Each fake names the statement it
   stands in for; they must be updated alongside
   `relay/infrastructure/persistence/`.

2. **The end-to-end path stays with the smoke scripts.** These tests
   deliberately do not re-verify that Postgres accepts the writes or that
   Kafka round-trips a message. `week34_smoke.py` remains the gate for
   "all three row types visible in Postgres".

3. **Builders live in `_helpers.py`, not `conftest.py`.** pytest
   auto-loads `conftest.py`; importing it directly as a module risks a
   duplicate copy under a second name. `conftest.py` holds only fixtures.

4. **`tests/data_plane/__init__.py` exists** although the other test
   subdirectories have none, so `tests.data_plane._helpers` is importable
   by absolute path.

5. **`asyncio_mode = "auto"`** in `[tool.pytest.ini_options]`. The suite
   is async throughout and this avoids an `@pytest.mark.asyncio` on every
   test. Verified safe: no pre-existing test in the repository is async.

6. **`pytest` declared under `[project.optional-dependencies] dev`**,
   installed with `pip install -e ".[dev]"`. The sprint rule restricts
   `pyproject.toml` to adding `httpx`, so a test-only extra is the least
   invasive way to declare it — it does not touch the runtime dependency
   list.

7. **An autouse fixture clears `RELAY_ALLOW_PRIVATE_TARGETS` and
   `RELAY_WEBHOOK_SIGNING_SECRET`.** `week34_smoke.py` sets the former,
   and a value left in the developer's shell would silently disable the
   egress guard and mask a regression.

8. **`security._configured_keys.cache_clear()` around every auth test.**
   It is `lru_cache`d, so the first test's key set would otherwise leak
   into the rest of the module.

**Why**

Tests were written to fail for the original bug, not merely to pass
against current behaviour. Each was verified non-vacuous by reverting the
corresponding fix in a scratch copy of the tree and confirming a failure:

| Reverted behaviour | Caught by |
|---|---|
| `commit(message.offset + 1)` instead of the watermark | `test_out_of_order_completion_does_not_skip_the_slow_message`, `test_commit_never_advances_past_the_oldest_in_flight_offset` |
| Empty POST body | `test_request_body_is_the_event_json`, `test_payload_survives_the_round_trip` |
| No RFC 6598 blocklist | `test_rfc6598_shared_address_space_is_rejected` |
| `owner_id` taken from the request body | `test_owner_id_comes_from_the_key_not_the_body` |
| Committing the offset when fan-out fails | `test_repository_failure_withholds_the_commit`, `test_partial_fan_out_failure_withholds_the_commit` |
| No `max_delay_seconds` cap | `test_delay_never_exceeds_max_delay_seconds`, `test_ten_attempts_then_failed_with_the_full_delay_sequence` |

Two assertions were wrong on the first pass and the implementation was
right, which is worth recording:

- Committing watermark `0` while offset 0 is still in flight is correct,
  not a bug — offset 0 acknowledges nothing. The invariant to assert is
  that the committed offset never *exceeds* the oldest in-flight offset,
  not that no commit occurs.
- `_PartitionProgress.completed` is pruned to empty after a full in-order
  drain, not down to the last offset.

**One real defect surfaced**

`test_url_guard.py` failed against `http://100.100.100.200/`, Alibaba
Cloud's metadata endpoint. CPython 3.12 reports no `is_*` flag for RFC
6598 space (`100.64.0.0/10`), so the guard allowed it. Fixed by adding
`BLOCKED_NETWORKS` to `relay/infrastructure/url_guard.py`; see ADR 0017.
IPv4-mapped, NAT64, and 6to4 encodings were checked at the same time and
are already covered by `is_private` / `is_reserved`.

**Commands**

```bash
pip install -e ".[dev]"

# Person A unit/integration suite -- no Docker required
.venv/bin/python -m pytest tests/data_plane -q

# Whole repository, including Person B and C
.venv/bin/python -m pytest tests -q

# End-to-end, requires docker compose up -d && alembic upgrade head
.venv/bin/python scripts/week34_smoke.py
./scripts/api_smoke.sh
```

**Not covered**

- Real Kafka rebalance and partition reassignment. `_PartitionProgress`
  is per `TopicPartition`, but revocation mid-flight is untested.
- Real Postgres constraint and trigger behaviour, including the ADR 0019
  completion-only update trigger. The fakes model the intent; only
  Alembic plus a live database proves it.
- Connection-pool exhaustion under sustained load.
