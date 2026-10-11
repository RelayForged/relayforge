# Person B (Sam) — Implementation Plan

Scope: everything assigned to Person B in `docs/demo_implementation_plan.md` (T2, T3, T4, T5, T8, T9, T12 as owner; T1, T6, T7, T10, T11, T13 as support).
Written against the code on `feature/research-contribution` at `1b4c32a`. Every claim in section 1 was checked by reading the code or running it.

---

## 1. Where the code differs from the source plan

These change the size or shape of the work. Read them before the task list.

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| F1 | The dashboard `/ws` stream is synthetic. It is fed by `DashboardDemoSource`, not by the real pipeline. `delivery_events` is always `[]`. | `relay/dashboard/websocket.py:70`, `relay/dashboard/adapter.py:137-144` | Scenario buttons would not move the charts. T1's "verify WS payload" and T3's "add `eri_sub_scores`" are really "rewire `/ws` to `state_store`". This is the biggest hidden item and blocks C. |
| F2 | The simulator has 4 modes, not 6. There is no `success`, `mixed`, `auth_failure` or `connection_reset`. `timeout` only sleeps `latency_ms` and then returns 200. | `simulator/config.py:6-10`, `simulator/service.py:19-20` | T2 presets `success` and `mixed` do not validate today. A timeout preset needs `latency_ms` above the worker timeout (5 s default). |
| F3 | Exhausted deliveries are marked `FAILED`, not `DEAD_LETTER`. `DEAD_LETTER` is only set for a missing or inactive endpoint. Under PAUSE/QUARANTINE a delivery stays `PENDING` with no attempt row, the Kafka offset is committed, and nothing ever picks it up again. | `relay/data_plane/retry_manager/manager.py:91-93`, `relay/data_plane/dispatcher/main.py:352-356, 444-471` | "DLQ panel fills during Full Outage" will not happen with a `DEAD_LETTER`-only query. The DLQ must be defined as `FAILED` + `DEAD_LETTER` + parked `PENDING`. |
| F4 | Republishing a dead event to `events.business` is a no-op. The delivery is unique on `(event_id, subscription_id)`, terminal deliveries return early, and attempt 1 is already claimed. | `dispatcher/main.py:333-336, 358-362`, `persistence/models.py:104-108` | The replay design in T5 does not work as written. See D1. |
| F5 | Restore probably does not un-quarantine on the real stack. `restore_endpoint` only flips in-memory state. The cached QUARANTINE policy is still returned by `get_current_policy`, and the dispatcher re-caches it on every dispatch, so its 300 s TTL keeps resetting. Restore also publishes no policy event, so the dashboard cannot see `RECOVERING`. | `aede/service.py:106-113, 129-133`, `dispatcher/main.py:501-509` | Suspected, not reproduced: the only passing run used a mock dashboard and mock transport (`demo/LIVE_REPORT.md`, `tests/integration/test_e2e_demo_flow.py`). Reproduce on Day 1. The Recovery button and T11 depend on it. |
| F6 | Benchmark results never reach the chart API. `BenchmarkRunner` writes to `benchmark.metrics.benchmark_store`. The router reads a different object, `relay.api.chart.stores.benchmark_store`. | `benchmark/runner.py:374`, `relay/api/chart/router.py:20` | `GET /api/chart/benchmark/results` is always empty. T4 must bridge the two. |
| F7 | Scenario 10 does not show "C wins on all metrics". Measured with seed 42: success rate is 0.6423 for A, B and C. Wasted retries are 930 / 626 / 428. `recovery_time_events` is 0 / 0 / 187. | Ran `BenchmarkRunner` on scenario 10 | The KPI cards in T4 (↑28% success, ↓65% MTTR) are placeholders that the data does not support. Cards must be computed, and you need to pick the metrics. See D3. |
| F8 | The ERI thresholds in T1 (30/60/80/90) are not the live ones. Live values are 75/95, 55/85, recovery 80, quarantine 45 in compose. | `aede/service.py:82-89`, `docker-compose.yml:131` | C must draw bands from the payload, not constants. The WS contract carries `thresholds`. |
| F9 | The frontend connects to `ws://127.0.0.1:8000/ws`. Port 8000 is the ingestion API; the dashboard is on 8090. No CORS middleware exists on any app. Nothing in compose serves the React build. | `App.jsx:31`, `docker-compose.yml:178` | Owned by C and A, but it blocks the demo. Listed in section 6. |
| F10 | `relay/kernel/interfaces/delivery_adapter.py` does not exist. `prometheus_client` is not a dependency. Worker and AEDE metrics live in the dispatcher process, which has no HTTP server. | `relay/kernel/interfaces/`, `pyproject.toml` | T12 creates the file. T7 needs a metrics port on the dispatcher. |
| F11 | Explainability records are held in memory only. The source plan lists them as persisted to Postgres. | `persistence/explainability_repo.py` has a Protocol and an in-memory class only | Outside the T-list, but it is in your area and it is an MVP checklist claim. Not planned here; raise it with the team. |

The benchmark is fast: all 8 held-out scenarios × 3 strategies ran in 0.44 s and the multi-endpoint comparison in 0.59 s. No background thread is needed for T4 or T8.

---

## 2. Decisions that are yours

Each has a recommendation. The plan below assumes the recommendation.

**D1. Replay mechanism (T5).** Recommended: a new topic `events.internal.replay` consumed by the dispatcher.
- The API-side `ReplayManager` selects deliveries and publishes one `ReplayWork` per delivery. It never builds a policy; the API process has no AEDE.
- The dispatcher handles each one through the normal `_dispatch_one` path, so AEDE still decides the policy and isolation is respected.
- A parked `PENDING` delivery is re-dispatched as is (attempt 1 is free).
- A `FAILED` or `DEAD_LETTER` delivery gets a new `Event` (same type, payload and `correlation_id`; `causation_id` = original `event_id`), dispatched to that one subscription only. This gives a new Delivery with attempt 1 and leaves the old rows untouched.
- Cost: one line in `PLATFORM_TOPICS` and a handler in the dispatcher. The alternative, a new attempt number on the old delivery, mutates its status and gets no retry budget because `attempt_number >= max_retries`.

**D2. Does operator replay honor `replay_policy.allowed`?** The synthesizer sets `allowed=True` only in `RECOVERING`. The demo script replays at 7:00, after the endpoint is back to `AVAILABLE`, where `allowed=False`. Recommended: operator replay is gated by isolation action only (no HTTP under PAUSE/QUARANTINE) and uses `replay_policy.batch_size`. Document `allowed` as governing automatic replay. The alternative is changing the AVAILABLE profile in the synthesizer, which touches benchmark-validated behavior.

**D3. Which benchmark metrics the KPI cards show.** Given F7, recommended: wasted retries and total retry delay as the headline, success rate shown without a "wins" claim, and no MTTR card unless you define recovery time for A and B, which have no state machine and report 0. The demo script line "C wins on all metrics" needs rewording either way.

**D4. Where `hmac_required` comes from (T9).** The source plan has the synthesizer read `RELAY_HMAC_REQUIRED`. An env read inside `synthesize_policy` is a hidden input to a function that must be deterministic. Recommended: read it once at process start, the same way `AEDE_QUARANTINE_THRESHOLD` is read in `AEDEService.__init__`, and pass `hmac_required` as an explicit parameter. Strategies A and B and the Tier 3 fallback take the same parameter so signing does not disappear during the fallback demo.

**D5. How the signature badge gets its data (T9).** Attempts do not store headers, and adding a column means a migration plus the update-trigger rules in ADR 0019. Recommended: the simulator keeps a small ring buffer of received requests with the verification result, exposed at `GET /simulator/deliveries`. C joins on `attempt_id`. The badge then shows real receiver-side verification.

**D6. `POST /api/chart/benchmark/run` lives on the dashboard backend**, which CLAUDE.md says is strictly read-only. Recommended: keep it there as the source plan says, because trigger and results must share a process. It runs a synthetic simulation and writes no platform data. Say so in the docstring.

---

## 3. Contracts for Person C (publish on Day 1)

C builds against these. Ports: API 8000, dashboard 8090, simulator 8088 on the host.

### 3.1 WebSocket — `ws://127.0.0.1:8090/ws?endpoint_id=<id>`

`endpoint_id` is optional; the default is the most recently evaluated endpoint. One message per second.

```json
{
  "type": "state_update",
  "source": "live",
  "endpoint_id": "…",
  "state": "UNSTABLE",
  "eri": 44.8,
  "eri_history": [
    {"timestamp": "…", "value": 44.8, "state": "UNSTABLE", "sub_scores": {"success_rate": 0, "latency": 0, "consecutive_failures": 0, "timeout_rate": 0, "http_5xx_rate": 0, "queue_depth": 0, "recovery_trend": 0}}
  ],
  "eri_sub_scores": {"success_rate": 0, "latency": 0, "consecutive_failures": 0, "timeout_rate": 0, "http_5xx_rate": 0, "queue_depth": 0, "recovery_trend": 0},
  "eri_weights": {"success_rate": 0.35, "…": 0},
  "thresholds": {"degradation_threshold": 75, "degradation_recovery_threshold": 95, "unstable_threshold": 55, "unstable_recovery_threshold": 85, "quarantine_threshold": 45, "recovery_threshold": 80},
  "delivery_events": [{"id": "…", "timestamp": "…", "type": "DELIVERY_FAILURE", "event_id": "…", "delivery_id": "…", "attempt_id": "…", "attempt_number": 2, "http_status": 500, "success": false, "error_message": "HTTP 500", "status": "PENDING"}],
  "policy_decision": {},
  "policy_explanation": {}
}
```

- `policy_decision` and `policy_explanation` keep their current shape (`relay/dashboard/models.py`). `policy_explanation` already carries `state_before`, `state_after`, `state_changed`, `reason` and `triggered_thresholds`, which is all T6 and T11 need.
- Sub-score keys are the ones `state_store` already uses (`relay/api/chart/stores.py:72-80`). Values are 0.0–1.0 (1.0 = healthy), as on `ERIScore`; `eri_weights` uses the same keys.
- `eri` becomes a float. It is an int today.
- `source` is `"live"` or `"synthetic"`. C shows a visible marker when it is synthetic.

### 3.2 REST

| Call | Host | Body / query | Returns |
|---|---|---|---|
| `GET /demo/scenarios` | API | — | `[{id, label, simulator: {mode, rate, latency_ms}, expects}]` |
| `POST /demo/scenario` | API | `{scenario: "full_outage"}` | `{active_scenario, endpoint_id, subscription_id, simulator, publishing, rate_per_sec}` |
| `GET /demo/status` | API | — | same shape; `active_scenario` is null when idle |
| `POST /demo/stop` | API | — | stops the publisher |
| `GET /api/chart/dlq` | dashboard | `endpoint_id?`, `limit?` | `{items: [{delivery_id, event_id, event_type, endpoint_id, reason, attempt_count, last_error, failed_at}]}` where `reason` is `FAILED`, `DEAD_LETTER` or `PARKED` |
| `POST /replay` | API | `{delivery_ids?: [], endpoint_id?: "", limit?: 50}` | 202 `{replay_id, requested, skipped: [{delivery_id, why}]}` |
| `POST /api/chart/benchmark/run` | dashboard | `{scenario_ids?: [], strategies?: [], seed?: 42}` | `{runs: [...], comparison: {c_vs_a: {...}, c_vs_b: {...}}}` |
| `GET /api/chart/benchmark/results` | dashboard | — | existing shape plus the new optional fields |
| `GET /api/chart/benchmark/multi-endpoint` | dashboard | — | `{worker_pool_size, strategies: [{strategy, quarantine_triggered, failing: {...}, healthy: {...}}]}` |
| `GET /simulator/deliveries` | simulator | `limit?` | `[{attempt_id, event_id, received_at, signature, signature_status, response_status}]` |
| `POST /endpoints/{id}/restore` | API | — | unchanged |

Scenario ids: `normal`, `early_degradation`, `instability`, `full_outage`, `recovery`, `rate_limiting`, `auth_failure`, `network_partition`. Presets are defined on the server; C sends only the id.

---

## 4. Work items, in build order

### 4.1 Live WebSocket feed (T1 support, T3, T6 support)

Files: `relay/dashboard/websocket.py`, `relay/dashboard/adapter.py`, `relay/dashboard/models.py`, `relay/dashboard/main.py`, `relay/infrastructure/persistence/delivery_attempt_repository.py`.

1. In `send_state_update`, read from `state_store`: latest state, `get_eri_history`, latest evaluation, policy and explainability record. Build the payload in 3.1. Keep `build_state_update` as the single contract builder and extend it.
2. Add `eri_sub_scores`, `eri_weights`, `thresholds` and `endpoint_id`. Put `state` and `sub_scores` on every history point (`ERISnapshot` already has both).
3. Fill `delivery_events` from Postgres with a new read-only repository method `list_recent_by_endpoint(endpoint_id, limit)` that joins `delivery_attempts` to `deliveries`. Reuse `get_delivery_event` in the adapter.
4. Keep `DashboardDemoSource` behind an explicit switch, `DASHBOARD_SYNTHETIC_SOURCE=true`, and set `source` accordingly. No silent fallback: synthetic data must never pass for live data in front of examiners.
5. Add CORS middleware to the dashboard app, with origins from `RELAY_CORS_ORIGINS`.
6. Tests: update `tests/dashboard/test_websocket.py` and `test_adapter.py`. New cases: payload from a seeded `state_store`, empty store, synthetic switch.

### 4.2 Simulator modes and scenario engine (T2)

Files: `simulator/config.py`, `simulator/service.py`, `simulator/main.py`, new `relay/api/demo.py`, `relay/api/main.py`, `relay/api/events.py`, `relay/infrastructure/persistence/endpoint_repository.py`.

1. Simulator: add `SUCCESS`, `MIXED`, `AUTH_FAILURE` (401) and `CONNECTION_RESET`. `MIXED` alternates 500 and a slow 200 on failing requests. Keep the `simulate_webhook(config)` signature so `tests/test_simulator.py` still passes.
2. `CONNECTION_RESET` has to drop the connection without a response; returning or raising makes uvicorn send a 500. Abort the transport from the request cycle. The worker will record this as a transport error with no status and `timed_out=False`, so the source plan's "timeout sub-metric dominates" is wrong for this preset. Set the expected-outcome text from what you observe.
3. Add `GET /simulator/config` so the active config is readable.
4. `relay/api/demo.py`:
   - Presets as a module-level table keyed by scenario id.
   - Bootstrap: find or create the demo endpoint (`DEMO_TARGET_URL`, default `http://simulator:8080/webhook`) and its subscription. This must be idempotent across restarts, because Postgres persists and a second subscription doubles every delivery. Add a lookup by `(owner_id, target_url)` to the endpoint repository.
   - `POST /demo/scenario`: push the config to `SIMULATOR_URL/simulator/config`, then start or retarget one publisher task at 2 events/s. `recovery` also publishes the restore control message.
   - The publisher goes through the same persist-then-publish path as `POST /events`. Extract that from `ingest_event` into a helper both callers use. No event is published from memory alone.
   - The publisher task is held in process memory. Expose it through `GET /demo/status` and cancel it in the app lifespan so it is visible state, not hidden state.
   - Mount the router only when `RELAY_DEMO_ENABLED=true`, and keep `require_owner` on it. It is a control surface.
5. Timeout preset: `latency_ms` must exceed the worker timeout. The preset uses `latency_ms=6000`, which is above the 5 s default, so it is a real timeout whatever the dispatcher is set to. Ask A to set `RELAY_WEBHOOK_TIMEOUT_SECONDS=2` on the dispatcher so an outage collapses in seconds rather than tens of seconds.
6. Add CORS to the API and simulator apps.
7. Tests: one per simulator mode; demo router with a mocked simulator and publisher (preset validation, idempotent bootstrap, single publisher task, stop).

### 4.3 Restore path (T11 support, needed by the Recovery button)

Files: `relay/control_plane/aede/service.py`, `relay/data_plane/dispatcher/main.py`.

1. Reproduce F5 on the compose stack first: quarantine, restore, keep publishing, watch whether HTTP resumes.
2. If confirmed: on restore, AEDE synthesizes the `RECOVERING` policy, replaces the cached policy, saves an explainability record with reason "admin restore", and publishes a policy evaluation event. The dispatcher passes `computed_at` in; AEDE does not read the clock.
3. The dashboard then sees `RECOVERING` immediately, which is what clears C's banner.
4. Tests: restore replaces the cached policy; restore emits a record and an event; restore on a non-quarantined endpoint is a no-op.

### 4.4 ReplayManager and DLQ (T5)

Files: new `relay/data_plane/replay/manager.py`, `relay/api/endpoints.py`, `relay/api/chart/router.py` and `schemas.py`, `relay/sdk/client.py` and `models.py`, `relay/infrastructure/persistence/delivery_repository.py`, `relay/infrastructure/messaging/admin.py`, `relay/data_plane/dispatcher/main.py`.

1. Repository: `get_dead_letters(endpoint_id, limit)`. The source plan puts it on the attempt repository, but status lives on `deliveries`, so it belongs on `DeliveryRepository`. It returns `FAILED`, `DEAD_LETTER` and parked `PENDING` (no attempt row, older than a short grace period). It excludes deliveries already replayed, found by an anti-join on a child event whose `causation_id` is the original `event_id`. Agree the query with Person A.
2. `ReplayManager.replay_dead_letters(...)`: select, cap at `limit`, publish one `ReplayWork` per delivery to `events.internal.replay`, return counts. `ReplayWork` is a local Pydantic model, following the `RetryWork` precedent.
3. Dispatcher: subscribe to the new topic and add `_accept_replay`. Run it as a tracked task, and commit the offset only after the work is persisted, like `_run_scheduled`. Do not copy `_accept_endpoint_control`, which commits in `finally` and would drop a replay on failure.
4. `POST /replay` with `require_owner` and the same ownership check as restore. `GET /api/chart/dlq` on the dashboard, read-only.
5. SDK: `replay_dead_letters(endpoint_id=None, delivery_ids=None)`.
6. Every replay request logs a structured line with `replay_id`, counts and correlation ids.
7. Tests: unit tests for selection and exclusion; dispatcher tests for both branches (parked re-dispatch, failed creates a new event and delivery with attempt 1); an assertion that the original rows are byte-identical afterwards; a quarantined endpoint re-parks instead of sending; SDK test with a mock transport.

### 4.5 Benchmark API (T4)

Files: `relay/api/chart/router.py`, `schemas.py`, `stores.py`.

1. `POST /api/chart/benchmark/run`: defaults are the 8 held-out scenarios, strategies A/B/C and seed 42. Run in `asyncio.to_thread`; it finishes in under a second.
2. Bridge F6: convert each `BenchmarkResult` with `summarise()` and store it in the chart `benchmark_store`. Clear earlier runs for the same `(scenario, strategy, seed)` so repeated clicks do not pile up bars.
3. Extend `BenchmarkRun` and `BenchmarkRunResponse` with optional `scenario_id`, `delivery_success_rate`, `total_wasted_retries`, `recovery_time_events`, `quarantine_count` and `total_delay_seconds`. Defaults keep `tests/unit/test_chart_api.py` passing.
4. `comparison` is computed from the runs, never hardcoded, using the metrics chosen in D3.
5. Tests: run endpoint populates the results endpoint; same seed gives identical output; comparison arithmetic.

### 4.6 HMAC signing surface (T9)

Files: new `relay/infrastructure/auth/signing.py`, `relay/infrastructure/auth/__init__.py`, `relay/data_plane/workers/webhook.py`, `relay/control_plane/aede/synthesizer.py`, `service.py`, `strategies/*.py`, `simulator/main.py`.

1. `compute_relay_signature(secret, timestamp, nonce, body)` and `verify_relay_signature(...)` with a constant-time compare and a max-age check where `now` is a parameter. The signed material stays `timestamp.nonce.body`.
2. The worker keeps `_apply_signature` and delegates the digest. The tests at `tests/data_plane/test_webhook_worker.py:280-436` must pass unchanged.
3. Apply D4: `hmac_required` is a parameter of `synthesize_policy`, default `False`, set from config in `AEDEService` and the strategies. Turn on `replay_protection` with it.
4. Simulator: read the raw body and headers, verify when a secret is set, log `SIGNATURE VERIFIED` or `SIGNATURE INVALID`, and return 401 on an invalid signature. Record each request in the ring buffer from D5.
5. Tests: sign/verify round trip, tampered body, stale timestamp; synthesizer with the flag on and off; simulator accepts valid and rejects forged.

### 4.7 Delivery adapter (T12)

Files: new `relay/kernel/interfaces/delivery_adapter.py`, new `relay/adapters/webhook/adapter.py`, `relay/data_plane/workers/webhook.py`, `relay/data_plane/dispatcher/main.py`.

1. Kernel: a `DeliveryAdapter` Protocol with `deliver(target_url, body, headers)` and `aclose()`, a small frozen response type, and kernel-level timeout and transport exceptions. No `httpx` in the kernel.
2. `WebhookDeliveryAdapter` takes over `_post`, `_get_client` and the pooled client (redirects stay off) and maps `httpx` errors to the kernel exceptions.
3. The worker takes `adapter=`. Keep the `client=` keyword working by wrapping it in an adapter, because every worker test injects `httpx.AsyncClient(MockTransport)`.
4. Leave the `assert_deliverable` call in the worker module. Tests monkeypatch it at `relay.data_plane.workers.webhook.assert_deliverable`.
5. The dispatcher constructs the adapter and injects it. Person A reviews.
6. Tests: the existing worker suite unchanged, plus a fake adapter proving the worker has no `httpx` dependency.

### 4.8 Metrics wiring (T7, your half)

Starts after Person A lands `relay/infrastructure/monitoring/metrics.py`.

1. Worker: count attempts by `endpoint_id` and status; observe the retry count when a delivery reaches a terminal status.
2. AEDE: set the ERI gauge on every evaluation and record state transitions.
3. The dispatcher process needs its own exporter (F10). Start one on a configurable port in `dispatcher/main.py` and ask A to add the scrape target.
4. Raise with A: `ENDPOINT_STATE` as a Gauge labelled `from_state, to_state` cannot express transitions. A transition counter plus a current-state gauge fits what Grafana needs.
5. Build `deploy/grafana/dashboards/relay.json`: ERI gauge, attempts by status, retry histogram, transition markers.

### 4.9 Multi-endpoint data (T8)

Files: `relay/api/chart/router.py`, `schemas.py`. The source plan lists no backend file for this, but C needs an endpoint.

1. `GET /api/chart/benchmark/multi-endpoint` calls `run_full_multi_endpoint_comparison()` and caches the result in process. It is deterministic.
2. Measured values C can expect for the healthy neighbor: throughput 1.39 / 1.98 / 16.58 events/s and mean queue wait 52.7 s / 39.3 s / 4.0 s for A / B / C. Only C triggers quarantine.

### 4.10 Typed events (T13, your half)

Files: `relay/kernel/events/policy_events.py`, `endpoint_events.py`, `replay_events.py`. Starts after A's `base.py`.

Frozen Pydantic models: `PolicyEvaluationCompleted`, `PolicyChanged`, `EndpointStateChanged`, `EndpointQuarantined`, `EndpointRestored`, `ReplayRequested`, `ReplayCompleted`. Tests cover immutability and JSON round trip. Switching the existing Kafka payloads to these types is a follow-up, not part of this plan.

### 4.11 Review-only items

- T10: review A's KEDA trigger on dispatcher consumer lag (group `relay-dispatcher`) and the probes. The dispatcher has no HTTP endpoint until 4.8 adds the metrics port.
- T1 and T6: covered by the contract in 3.1.

---

## 5. Schedule

| Day | Work |
|---|---|
| 1 | Send section 3 to C. 4.1 live WebSocket. Reproduce F5. Start 4.2 (simulator modes). |
| 2 | Finish 4.2 (scenario engine). 4.3 restore fix. Start 4.4. |
| 3 | Finish 4.4 replay and DLQ. 4.5 benchmark API. |
| 4 | 4.6 HMAC. 4.7 delivery adapter. |
| 5 | 4.8 metrics and Grafana dashboard. |
| 6 | 4.9 multi-endpoint. 4.10 typed events. |
| 7 | Full-stack run on compose, timing the demo script. |
| 8 | Buffer and rehearsal. |

Compared with the source schedule, the WebSocket rewire and the restore check move to the front because both block C and the scenario buttons, and HMAC moves from Day 3 to Day 4 to make room for the larger T5.

---

## 6. What you need from others

**Person A (`docker-compose.yml` and infrastructure)**
- `api`: `SIMULATOR_URL=http://simulator:8080`, `DEMO_TARGET_URL`, `RELAY_DEMO_ENABLED=true`, `RELAY_CORS_ORIGINS`.
- `dispatcher`: `RELAY_WEBHOOK_SIGNING_SECRET`, `RELAY_HMAC_REQUIRED=true`, `RELAY_WEBHOOK_TIMEOUT_SECONDS=2`, the metrics port, and a Prometheus scrape target for it.
- `simulator`: `RELAY_WEBHOOK_SIGNING_SECRET`.
- `dashboard`: `RELAY_CORS_ORIGINS`.
- `prometheus_client` in `pyproject.toml`; `metrics.py` before Day 5; `kernel/events/base.py` before Day 6.
- Review: the new topic in `PLATFORM_TOPICS`, the dead-letter query, the `ingest_event` extraction, the adapter split.
- A decision on what serves the React build in compose (F9).

**Person C**
- Point the WebSocket at port 8090 and send scenario and replay calls to 8000.
- Draw ERI bands from `thresholds` in the payload (F8).
- Compute nothing in the KPI cards; render `comparison` from the API.
- Show a marker when `source` is `synthetic`.

---

## 7. Done criteria

- `pytest` green, including the 331 existing tests, with new tests per item above.
- On the compose stack, with no terminal use after `docker compose up`:
  - each of the 8 scenario buttons moves the live ERI chart and state;
  - Full Outage fills the DLQ list;
  - Recovery returns the endpoint to `AVAILABLE`;
  - Replay All delivers the listed events and the original delivery and attempt rows are unchanged;
  - the simulator logs `SIGNATURE VERIFIED`;
  - the benchmark and multi-endpoint endpoints return data;
  - stopping AEDE still delivers under the fallback policy.
- Recovery timing is the main rehearsal risk. The ERI window holds 100 observations and `RECOVERING` runs at `max_parallel=1`, so the climb back above the recovery threshold may not fit the 30 seconds the demo script allows. The knobs are publish rate, window size and the script's timing.
