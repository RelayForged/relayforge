# RelayForge — Complete Implementation Plan (Demo + Architecture Gaps)

> **Goal**: Make the platform visually demonstrable in a live academic setting AND close all
> Tier 1 architecture gaps so the codebase matches the claims in `arch.md`.
> A person who has never seen the project must understand what is happening within 5 seconds
> of watching each panel. Stubs must be code, not `.gitkeep`.

---

## ✅ What Is Already Done

### Research Core (100% complete)
| Component | Location | Status |
|---|---|---|
| ERI calculator with 7 configurable sub-metrics and optimal weights | `relay/control_plane/eri/calculator.py` | ✅ Done |
| 5-state endpoint state machine with hysteresis (AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED → RECOVERING) | `relay/control_plane/state_machine/transitions.py` | ✅ Done |
| Policy Synthesizer — immutable `DeliveryPolicy` from state + ERI | `relay/control_plane/aede/synthesizer.py` | ✅ Done |
| AEDE Service — closed telemetry feedback loop | `relay/control_plane/aede/service.py` | ✅ Done |
| Strategy A (static exponential backoff baseline) | `relay/control_plane/aede/strategies/static.py` | ✅ Done |
| Strategy B (adaptive backoff, no state model, dynamic max_retries) | `relay/control_plane/aede/strategies/adaptive.py` | ✅ Done |
| Strategy C (full AEDE adaptive policy) | wired via feature flags | ✅ Done |
| Explainability records — every policy decision persisted to Postgres with thresholds, metrics, reason text | `relay/control_plane/explainability/record.py` | ✅ Done |
| Two-stage ERI weight calibration (hand-picked sweep + genetic algorithm over 6 calibration scenarios, verified on 8 held-out scenarios) | `benchmark/genetic_optimizer.py`, `benchmark/optimize_weights.py` | ✅ Done |

### Benchmark & Evaluation (100% complete)
| Component | Location | Status |
|---|---|---|
| All 14 benchmark failure scenarios (6 calibration, 8 held-out) | `benchmark/scenarios.py` | ✅ Done |
| Multi-seed benchmark runner (5 seeds: 42, 123, 456, 789, 1337) | `benchmark/run_experiments.py` | ✅ Done |
| Statistical significance testing (Mean ± Std, 95% CI, paired t-test p-values for A vs C and B vs C) | `benchmark/run_experiments.py` | ✅ Done |
| Weight sensitivity analysis (±10%/±20% perturbations across all 7 ERI weights) | `benchmark/sensitivity_analysis.py` | ✅ Done |
| Multi-endpoint contention simulation (noisy-neighbor quantification) | `benchmark/multi_endpoint.py` | ✅ Done |
| Leave-one-out calibration integrity check | `benchmark/loo_calibration_check.py` | ✅ Done |
| Ablation study | `benchmark/ablation.py` | ✅ Done |

### Data Plane (100% complete)
| Component | Location | Status |
|---|---|---|
| FastAPI Ingestion API (`POST /events`, `/endpoints`, `/subscriptions`, `/endpoints/{id}/restore`) | `relay/api/` | ✅ Done |
| Kafka producer (event ingestion → `events.business` topic) | `relay/infrastructure/messaging/producer.py` | ✅ Done |
| Kafka consumer / Dispatcher (reads business + retry topics, requests policy, assigns work) | `relay/data_plane/dispatcher/main.py` | ✅ Done |
| Stateless Webhook Worker (HTTP POST, telemetry collection, AEDE feedback) | `relay/data_plane/workers/webhook.py` | ✅ Done |
| HMAC-SHA256 payload signing — code exists, activated by `hmac_required` in policy | `relay/data_plane/workers/webhook.py` | ✅ Done (not wired in docker-compose yet → T9) |
| SSRF guard (blocks private IP targets in production) | `relay/infrastructure/url_guard.py` | ✅ Done |
| Retry Manager (schedules future attempts from policy intervals) | `relay/data_plane/retry_manager/manager.py` | ✅ Done |
| Dead Letter Queue routing (exhausted deliveries → `DEAD_LETTER` status in Postgres) | dispatcher + worker | ✅ Done |
| Three-tier policy fallback (AEDE live → Redis cache → Strategy A static defaults) | `relay/infrastructure/cache.py` + dispatcher | ✅ Done |
| Redis policy cache with TTL | `relay/infrastructure/cache.py` | ✅ Done |
| Feature flags (`AEDE_ENABLED`, `ERI_ENABLED`, `STATE_MACHINE_ENABLED`, `STATIC_RETRY_MODE`, `ADAPTIVE_RETRY_MODE`) | `relay/infrastructure/feature_flags.py` | ✅ Done |
| PostgreSQL repositories (Events, Endpoints, Subscriptions, Deliveries, Attempts, Telemetry, Explainability) | `relay/infrastructure/persistence/` | ✅ Done |
| Alembic DB migrations | `alembic.ini` + `relay/infrastructure/persistence/migrations/` | ✅ Done |

### Infrastructure & Deployment (partially complete)
| Component | Location | Status |
|---|---|---|
| Docker Compose stack (Kafka, PostgreSQL, Redis, API, Dispatcher, Simulator, Dashboard) | `docker-compose.yml` | ✅ Done |
| Multi-stage Dockerfile | `Dockerfile` | ✅ Done |
| Endpoint Simulator (all 6 failure modes: http_error, timeout, slow, rate_limit, connection_reset, auth_failure) | `simulator/` | ✅ Done |
| Prometheus & Grafana | — | ❌ Not in docker-compose → **T7** |
| Kubernetes manifests | — | ❌ `deploy/k8s/` does not exist → **T10** |

### Dashboard (partially complete)
| Component | Location | Status |
|---|---|---|
| WebSocket backend streaming live state updates | `relay/dashboard/main.py` | ✅ Done |
| Chart Data API (endpoints, ERI history, policy, explainability, benchmark results) | `relay/api/chart/router.py` | ✅ Done |
| React frontend — endpoint state badge, ERI line chart, delivery timeline, policy panel, explainability panel, fault injection controls | `relay/dashboard/frontend/src/App.jsx` | ✅ Basic done |
| ERI chart threshold bands + hysteresis lines | — | ❌ → **T1** |
| Scenario preset buttons | — | ❌ → **T2** |
| Sub-metric radar chart | — | ❌ → **T3** |
| Strategy A/B/C benchmark comparison tab | — | ❌ → **T4** |
| DLQ panel + replay button | — | ❌ → **T5** |
| State transition journal | — | ❌ → **T6** |
| Quarantine alert toasts | — | ❌ → **T11** |

### SDK & Domain Model (complete)
| Component | Location | Status |
|---|---|---|
| Python SDK (`publish_event`, `register_endpoint`, `create_subscription`, `get_event`) | `relay/sdk/client.py` | ✅ Done |
| `replay_dead_letters()` SDK method | — | ❌ → **T5** |
| Core domain entities (Event, Endpoint, Subscription, Delivery, DeliveryAttempt, DeliveryPolicy, ERIScore, EndpointState, Telemetry, ExplainabilityRecord, PolicyEvaluation) | `relay/kernel/` | ✅ Done |
| Typed internal event domain model | `relay/kernel/events/` | ❌ Empty stub → **T13** |

### Tests (331/331 passing)
| Suite | Count | Status |
|---|---|---|
| Unit tests | 280+ | ✅ All passing |
| Integration tests | 50+ | ✅ All passing (including live Kafka pipeline, e2e demo flow, telemetry loop) |
| Total | **331** | ✅ Exit code 0 |

### Research Report (complete)
All 7 chapters of `docs/report/` are written:
`01-introduction.md`, `02-related-work.md`, `03-system-design.md`,
`04-evaluation-methodology.md`, `05-benchmark-results.md`,
`06-discussion-threats-to-validity.md`, `07-conclusion.md`

---


## Gap Cross-Reference: What Was Missing vs. What's Now Covered

| Gap (from Architecture Audit) | Tier | Covered by Task |
|---|---|---|
| ERI chart has no threshold bands | Demo | **T1** |
| No scenario preset buttons | Demo | **T2** |
| No sub-metric radar chart | Demo | **T3** |
| No Strategy A/B/C benchmark tab | Demo | **T4** |
| `relay/data_plane/replay/` — empty stub; `replay_dead_letters()` not in SDK | **Tier 1** | **T5** |
| No DLQ panel in dashboard | Demo | **T5** |
| No live state transition journal | Demo | **T6** |
| `relay/infrastructure/monitoring/` — empty stub; no `/metrics` route; Prometheus/Grafana not in docker-compose | **Tier 1** | **T7** |
| Multi-endpoint noisy-neighbor view | Demo | **T8** |
| HMAC signing exists in worker but `relay/infrastructure/auth/` is an empty stub; no signing env in docker-compose; signing never enabled in policies; not visible in dashboard | **Tier 1** | **T9** |
| `deploy/k8s/` — does not exist; Kubernetes manifests listed as MVP checklist item | **Tier 2** | **T10** |
| No operational alert when endpoint enters QUARANTINED (notifications listed as deferred but dashboard alert is the agreed alternative) | **Tier 2** | **T11** |
| `relay/adapters/webhook/` — empty stub; HTTP dispatch embedded directly in `WebhookWorker` | **Tier 1** | **T12** |
| `relay/kernel/events/` — empty stub; no typed internal event domain model | **Tier 1** | **T13** |

---

## Priority Order

```
T1 → T2 → T3 → T5 → T4 → T6 → T9 → T7 → T10 → T11 → T8 → T12 → T13
```

Rationale:
- T1–T6: highest visual return for the live demo
- T9: HMAC surface is visible in the dashboard + demo delivery headers
- T7: Prometheus/Grafana closes Tier 1 monitoring stub
- T10: Kubernetes closes the MVP checklist item (required for submission)
- T11: Alert notifications visible in demo but low implementation cost
- T12–T13: Architecture correctness; not visible in demo but needed for arch claim

---

## T1 — ERI Chart with Threshold Bands & State Color Coding
**Demo impact: Highest. No backend changes. Pure frontend.**

### What to build
Replace the plain `LineChart` with a `ComposedChart`:

- **Colored `ReferenceArea` bands** pinned to ERI thresholds:
  - 0–30 → `#ef444422` Red (QUARANTINED zone)
  - 30–60 → `#f9731622` Orange (UNSTABLE zone)
  - 60–80 → `#eab30822` Yellow (OBSERVED_DEGRADATION zone)
  - 80–90 → `#86efac22` Light green (RECOVERING zone)
  - 90–100 → `#22c55e22` Bright green (AVAILABLE zone)
- **Two `ReferenceLine`s**: degradation at 80 (yellow dashed), recovery at 90 (green dashed) — shows hysteresis gap visually
- **ERI line color** changes dynamically to match current state
- **State transition dots** — colored dots on the line where state changed (red dot = entered QUARANTINED, etc.)
- **Three sparkline sub-charts** below the main ERI line: `success_rate`, `timeout_rate`, `consecutive_failures`

### Backend change
Backend `eri_history` array items already contain `state` via `ERISnapshot.state`. Verify that
`relay/dashboard/main.py`'s WebSocket broadcast includes `"state"` inside each `eri_history` item.

### Files
- `relay/dashboard/frontend/src/App.jsx` — replace `LineChart` with `ComposedChart` + bands
- `relay/dashboard/frontend/src/App.css` — band label styles

---

## T2 — Scenario Preset Buttons (The One-Click Demo Panel)
**Demo impact: Critical. Eliminates all terminal interaction during the demo.**

### What to build
A prominent **"Demo Scenarios" control panel** with large labeled buttons. Each button:
1. Calls `POST /demo/scenario` on the backend to configure the simulator
2. Starts auto-publishing events at 2/second to keep the pipeline moving
3. Highlights the active scenario name in a persistent banner

| Button | Simulator config | Audience observes |
|---|---|---|
| ▶ Normal | `{mode:"success", rate:0}` | ERI=100, AVAILABLE |
| ⚠ Early Degradation | `{mode:"mixed", rate:0.4, latency_ms:300}` | ERI→72, OBSERVED_DEGRADATION |
| 🔴 Instability | `{mode:"http_error", rate:0.75}` | ERI→42, UNSTABLE, backoff visible |
| 💀 Full Outage | `{mode:"timeout", rate:1.0}` | ERI collapses, QUARANTINED, HTTP halts |
| 🔄 Recovery | `{mode:"success", rate:0}` | RECOVERING probe, ERI climbs above 90 |
| ⚡ Rate Limiting | `{mode:"rate_limit", rate:0.8}` | 429s → success_rate radar spike |
| 🔑 Auth Failure | `{mode:"auth_failure", rate:1.0}` | HTTP 401, different ERI sub-metric pattern |
| 📡 Network Partition | `{mode:"connection_reset", rate:1.0}` | Timeout sub-metric dominates ERI |

### Backend changes
**`relay/api/demo.py`** (new file):
```python
@router.post("/demo/scenario")
async def activate_scenario(scenario: ScenarioRequest):
    # 1. POST /simulator/config on the simulator service
    # 2. Start background event publishing loop at 2 events/second
    # 3. Return {"active_scenario": scenario.name}
```

**`simulator/main.py`**: Ensure all 6 failure modes fully wired — `rate_limit` (429),
`auth_failure` (401/403), `connection_reset` (TCP RST) must affect correct ERI sub-metrics.

### Files
- `relay/api/demo.py` — **NEW**
- `relay/api/main.py` — include demo router
- `simulator/main.py` — complete all 6 failure modes
- `relay/dashboard/frontend/src/App.jsx` — scenario buttons panel

---

## T3 — Sub-Metric Radar Chart (The "Why" Visualizer)
**Demo impact: High. Shows WHICH signal triggered the policy change.**

### What to build
A **7-axis `RadarChart`** (one axis per ERI sub-metric), updated live from the WebSocket:

```
          success_rate (×0.35)
               ●
    recovery  / \  latency
    (×0.05)  /   \  (×0.20)
            /     \
  queue ──●         ●── consec_failures
  (×0.05)  \     /    (×0.20)
             \   /
    http_5xx  \ /  timeout
    (×0.05)    ●   (×0.10)
```

Each axis shrinks toward the center as that sub-metric degrades. During a timeout scenario
the `timeout` axis visually collapses while `success_rate` stays high — proving the signal
was timeout-specific, not a general failure.

### Backend change
`relay/dashboard/main.py` WebSocket broadcast must include `eri_sub_scores` dict alongside
the `eri` value. `ERIScore` already stores these — just ensure they're serialized in the WS payload.

### Files
- `relay/dashboard/main.py` — add `eri_sub_scores` to state_update broadcast
- `relay/dashboard/frontend/src/App.jsx` — `RadarChart` component

---

## T4 — Strategy A/B/C Benchmark Tab
**Demo impact: High. This is the quantitative research claim visualization.**

### What to build
A **"Benchmark" tab** with:

1. **Run buttons** per strategy: `[▶ Run Scenario 10 — Strategy A]` `[▶ B]` `[▶ C (AEDE)]`
   - Each triggers `POST /api/chart/benchmark/run` with `{scenario_id, strategy}`
   - Live progress bar while running
2. **Grouped bar chart** (3 bars per scenario):
   - X-axis: held-out scenarios (3, 4, 5, 9, 10, 12, 13, 14)
   - Y-axis: selectable metric (retry count / success rate / MTTR / wasted retries)
   - Strategy A = red, B = blue, C = green
3. **KPI callout boxes**:
   ```
   C vs A                C vs B
   ↓73% retries          ↓41% retries
   ↑28% success rate     ↑12% success rate
   ↓65% MTTR             ↓22% MTTR
   ```

### Backend changes
```python
# relay/api/chart/router.py  (add)
@router.post("/benchmark/run")
async def run_benchmark_scenario(scenario_id: int, strategy: str):
    # runs benchmark/runner.py in a background thread
    # streams results into benchmark_store
    # returns {"status": "started", "run_id": "..."}
```

`/api/chart/benchmark/results` already exists — only the trigger endpoint is new.

### Files
- `relay/api/chart/router.py` — add `POST /benchmark/run`
- `relay/dashboard/frontend/src/App.jsx` — Benchmark tab with GroupedBarChart

---

## T5 — ReplayManager + DLQ Panel (Full Event Lifecycle)
**Demo impact: High. The only remaining real backend work. Tier 1 gap.**

### Tier 1 Gap being closed
`relay/data_plane/replay/` is an empty stub. `relay_dead_letters()` does not exist in the SDK.
The MVP checklist explicitly requires "Replay from DLQ works without mutating history".

### What to build

**Backend — `relay/data_plane/replay/manager.py`** (new file):
```python
class ReplayManager:
    """Replays dead-lettered events. Creates NEW DeliveryAttempts — never mutates history."""

    async def replay_dead_letters(
        self,
        endpoint_id: str | None = None,
        limit: int = 50,
    ) -> int:
        # 1. Query PostgreSQL for DEAD_LETTER delivery attempts (scoped to endpoint if provided)
        # 2. For each: load original Event, create NEW DeliveryAttempt (fresh UUID, attempt_number=1)
        # 3. Publish Event back to events.business Kafka topic (re-enters pipeline)
        # 4. Update original DeliveryAttempt status to REPLAYED (immutable record stays, new record created)
        # 5. Return count queued
```

**Backend — new API endpoints**:
```python
# relay/api/endpoints.py  (add)
@router.post("/replay")
async def trigger_replay(endpoint_id: Optional[str] = None, limit: int = 50):
    count = await replay_manager.replay_dead_letters(endpoint_id, limit)
    return {"queued_for_replay": count}

# relay/api/chart/router.py  (add)
@router.get("/dlq")
async def get_dead_letters(limit: int = 50):
    # Query delivery_attempt_repository for status=DEAD_LETTER
    # Return list of {event_id, event_type, endpoint_id, failed_at, attempt_count}
```

**SDK — `relay/sdk/client.py`** (add):
```python
def replay_dead_letters(self, endpoint_id: str | None = None) -> dict:
    return self._request("POST", "/replay",
                         json_body={"endpoint_id": endpoint_id},
                         expected_status=200)
```

**Repository — `relay/infrastructure/persistence/delivery_attempt_repository.py`** (add):
```python
async def get_dead_letters(self, endpoint_id=None, limit=50) -> list[DeliveryAttempt]:
    # SELECT ... WHERE status = 'DEAD_LETTER' [AND endpoint_id = ...] ORDER BY failed_at DESC LIMIT ...
```

**Dashboard DLQ panel**:
- Live table of dead-lettered events (auto-refreshed every 5s via polling or WS push)
- Columns: Event ID (truncated), Event Type, Endpoint, Attempt #, Failed At
- `[Replay All]` button → calls `POST /replay`
- `[Replay Selected]` → passes specific event IDs

### Files
- `relay/data_plane/replay/manager.py` — **NEW**
- `relay/api/endpoints.py` — add `/replay`
- `relay/api/chart/router.py` — add `/dlq`
- `relay/sdk/client.py` — add `replay_dead_letters()`
- `relay/infrastructure/persistence/delivery_attempt_repository.py` — add `get_dead_letters()`
- `relay/dashboard/frontend/src/App.jsx` — DLQ panel component

---

## T6 — Live State Transition Journal
**Demo impact: Medium-High. Makes the full state machine journey readable. Frontend only.**

### What to build
A **"State History" panel** — a chronological journal of every state change, derived from
the WebSocket stream (no new backend code required):

```
🟢 23:12:04  AVAILABLE
⚠  23:12:31  AVAILABLE → OBSERVED_DEGRADATION   [ERI: 74.2 | success_rate fell to 61%]
🔴 23:12:52  OBSERVED_DEGRADED → UNSTABLE        [ERI: 44.8 | 8 consecutive failures]
💀 23:13:01  UNSTABLE → QUARANTINED              [ERI: 18.3 | quarantine threshold crossed]
🔄 23:14:30  QUARANTINED → RECOVERING            [admin restore triggered]
🟢 23:14:58  RECOVERING → AVAILABLE              [ERI: 91.2 | above recovery threshold (90)]
```

Frontend tracks previous state; when state changes it appends an entry including the
`triggered_thresholds` and `eri` value from the current WebSocket payload.

### Files
- `relay/dashboard/frontend/src/App.jsx` — state journal panel (frontend-only)

---

## T7 — Prometheus Metrics + Grafana Dashboard (Tier 1 Monitoring Gap)
**Closes: `relay/infrastructure/monitoring/` empty stub. MVP checklist item.**

### Tier 1 Gap being closed
`relay/infrastructure/monitoring/` has only `.gitkeep`. No `/metrics` endpoint exists.
Neither Prometheus nor Grafana is in `docker-compose.yml`.
The MVP checklist explicitly requires "Prometheus metrics are exposed and provisioned in Grafana".

### What to build

**1. `relay/infrastructure/monitoring/__init__.py`** (implement):
```python
# relay/infrastructure/monitoring/metrics.py  (new)
from prometheus_client import Counter, Gauge, Histogram

DELIVERY_ATTEMPTS = Counter(
    "relay_delivery_attempts_total",
    "Total delivery attempts",
    ["endpoint_id", "status"],          # status: success | failure | dead_letter
)
ERI_SCORE = Gauge(
    "relay_eri_score",
    "Current ERI score per endpoint",
    ["endpoint_id"],
)
ENDPOINT_STATE = Gauge(
    "relay_endpoint_state_transitions_total",
    "State machine transitions",
    ["endpoint_id", "from_state", "to_state"],
)
RETRY_COUNT = Histogram(
    "relay_retry_count",
    "Retry count per delivery",
    buckets=[0, 1, 2, 3, 5, 8, 10],
)
```

**2. Wire metrics in hot paths**:
- `relay/data_plane/workers/webhook.py` — emit `DELIVERY_ATTEMPTS`, `RETRY_COUNT` on each attempt
- `relay/control_plane/aede/service.py` — emit `ERI_SCORE`, `ENDPOINT_STATE` on each evaluation

**3. Expose `/metrics` on the FastAPI app**:
```python
# relay/api/main.py  (add)
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)
```

**4. `deploy/prometheus.yml`** (new):
```yaml
global:
  scrape_interval: 5s
scrape_configs:
  - job_name: relay-api
    static_configs:
      - targets: ["api:8000"]
  - job_name: relay-dashboard
    static_configs:
      - targets: ["dashboard:8090"]
```

**5. `docker-compose.yml`** (add):
```yaml
prometheus:
  image: prom/prometheus:v2.51.0
  ports: ["127.0.0.1:9090:9090"]
  volumes:
    - ./deploy/prometheus.yml:/etc/prometheus/prometheus.yml:ro
  depends_on: [api]

grafana:
  image: grafana/grafana:10.4.0
  ports: ["127.0.0.1:3000:3000"]
  environment:
    GF_SECURITY_ADMIN_PASSWORD: relay
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH: /var/lib/grafana/dashboards/relay.json
  volumes:
    - ./deploy/grafana/provisioning:/etc/grafana/provisioning
    - ./deploy/grafana/dashboards:/var/lib/grafana/dashboards
```

**6. `deploy/grafana/dashboards/relay.json`** — provisioned dashboard with:
- Live ERI gauge per endpoint
- Delivery success/failure rate time series
- Retry count histogram
- DLQ depth over time
- State transition event markers

### Files
- `relay/infrastructure/monitoring/metrics.py` — **NEW**
- `relay/data_plane/workers/webhook.py` — emit DELIVERY_ATTEMPTS, RETRY_COUNT
- `relay/control_plane/aede/service.py` — emit ERI_SCORE, ENDPOINT_STATE
- `relay/api/main.py` — add Instrumentator
- `deploy/prometheus.yml` — **NEW**
- `deploy/grafana/provisioning/datasources/prometheus.yml` — **NEW**
- `deploy/grafana/dashboards/relay.json` — **NEW**
- `docker-compose.yml` — add prometheus, grafana services

---

## T8 — Multi-Endpoint View (Noisy-Neighbor Proof)
**Demo impact: Medium. Visualizes the isolation argument side-by-side.**

### What to build
A **"Multi-Endpoint" tab** showing two endpoint panels side-by-side:
- Left: Endpoint A (experiencing failure → quarantine)
- Right: Endpoint B (nominal traffic)

Under **Strategy A**: Endpoint B's throughput bar drops as retry storms from A consume workers.
Under **Strategy C**: Endpoint A gets quarantined; Endpoint B throughput stays at 100%.

Powered by pre-computed results from `benchmark/multi_endpoint.py` — shown as a before/after
comparison, not a live simulation (live would require 2 separate registered endpoints which
is possible but adds setup complexity).

### Files
- `relay/dashboard/frontend/src/App.jsx` — Multi-Endpoint tab, side-by-side throughput bars

---

## T9 — HMAC Signing Surface (Tier 1 Auth Gap)
**Closes: `relay/infrastructure/auth/` empty stub. Makes signing visible and demoed.**

### Tier 1 Gap being closed
HMAC signing logic exists inside `relay/data_plane/workers/webhook.py` (the `_apply_signature()`
method is fully implemented). However:
- `relay/infrastructure/auth/` is an empty stub — signing helpers should live there
- `docker-compose.yml` has no `RELAY_WEBHOOK_SIGNING_SECRET` env var → signing never activates
- `HMAC_REQUIRED=true` is never set in any policy → `hmac_required` in `IntegrityPolicy` stays False
- The delivery timeline in the dashboard never shows signature headers → invisible to the audience

### What to build

**1. Move signing code to `relay/infrastructure/auth/signing.py`** (new):
```python
# relay/infrastructure/auth/signing.py
def compute_relay_signature(secret: str, body: bytes, timestamp: str, nonce: str) -> str:
    """HMAC-SHA256 over timestamp.nonce.body. Returns hex digest."""
    ...

def verify_relay_signature(secret: str, body: bytes, headers: dict) -> bool:
    """Subscriber-side verification helper (also used in tests)."""
    ...
```
- Import `compute_relay_signature` from `webhook.py` (refactor existing code, no logic change)
- `relay/infrastructure/auth/__init__.py` — export `compute_relay_signature`, `verify_relay_signature`

**2. Enable signing in Docker Compose**:
```yaml
# docker-compose.yml  (add to api and dispatcher services)
RELAY_WEBHOOK_SIGNING_SECRET: "demo-secret-key-change-in-prod"
RELAY_HMAC_REQUIRED: "true"
```

**3. Policy Synthesizer: respect `RELAY_HMAC_REQUIRED` flag**:
```python
# relay/control_plane/aede/synthesizer.py  (modify synthesize_policy)
hmac_required = os.environ.get("RELAY_HMAC_REQUIRED", "false").lower() == "true"
integrity_policy = IntegrityPolicy(hmac_required=hmac_required, ...)
```

**4. Dashboard delivery timeline**: Include `X-Relay-Signature` header value in the delivery
attempt detail so the audience can see `sha256=abc123...` in the browser:
```
[23:13:01] ✅ SUCCESS  HTTP 200  | X-Relay-Signature: sha256=8f3a2c... | Attempt #1
```

**5. Simulator webhook receiver**: Verify the signature and return 401 if wrong, log "SIGNATURE VERIFIED" if correct — making the signing observable in the simulator's response.

### Files
- `relay/infrastructure/auth/signing.py` — **NEW** (refactored from webhook.py)
- `relay/infrastructure/auth/__init__.py` — export signing helpers
- `relay/data_plane/workers/webhook.py` — import from `auth.signing` instead of inline
- `relay/control_plane/aede/synthesizer.py` — read `RELAY_HMAC_REQUIRED` env flag
- `docker-compose.yml` — add signing secret + flag to api and dispatcher services
- `simulator/main.py` — verify signature on incoming requests, log result
- `relay/dashboard/frontend/src/App.jsx` — show signature header in timeline

---

## T10 — Kubernetes Production Manifests (Tier 2 / MVP Checklist Item)
**Closes: `deploy/k8s/` does not exist. Explicitly listed as MVP checklist requirement.**

### Tier 2 Gap being closed
The execution plan MVP checklist (§E line 441) states:
> `[ ] Kubernetes production deployment manifests are complete and validated`

This is not deferred — it is a checklist gate for project completion.

### What to build
Create `deploy/k8s/` with:

```
deploy/
  k8s/
    namespace.yaml              # relay namespace
    configmap.yaml              # non-secret config (Kafka bootstrap, DB host, etc.)
    secret.yaml                 # template with placeholder secrets (not committed with values)
    kafka/
      statefulset.yaml          # Kafka StatefulSet (single-node for demo)
    postgres/
      statefulset.yaml          # PostgreSQL StatefulSet with PersistentVolumeClaim
    redis/
      deployment.yaml           # Redis Deployment
    relay-api/
      deployment.yaml           # relay-api Deployment (replicas: 2)
      service.yaml              # ClusterIP Service
      ingress.yaml              # Ingress with /api prefix
      hpa.yaml                  # HPA: scale on CPU (target 70%)
    relay-dispatcher/
      deployment.yaml           # relay-dispatcher Deployment (replicas: 2)
      hpa.yaml                  # HPA: scale on Kafka consumer lag metric
    relay-dashboard/
      deployment.yaml           # relay-dashboard Deployment
      service.yaml
    relay-simulator/
      deployment.yaml
      service.yaml
    prometheus/
      deployment.yaml
    grafana/
      deployment.yaml
  README-k8s.md                 # How to deploy: minikube start → kubectl apply -f deploy/k8s/
```

All manifests should use the same Docker image built by the project `Dockerfile`.
The dispatcher HPA should scale on Kafka consumer lag using the `kedacore/keda` external metrics adapter (or comment in the manifest that KEDA is required).

### Files
- `deploy/k8s/*.yaml` — **ALL NEW** (~15 files)
- `deploy/README-k8s.md` — **NEW**

---

## T11 — Quarantine Alert Notifications (Tier 2 / Dashboard Alert Panel)
**Closes: Notification Service is deferred for email/Slack, but the dashboard alert panel is the agreed MVP alternative.**

### What to build
When the endpoint state transitions to `QUARANTINED` or `UNSTABLE`, the dashboard must show a
visible alert — not just a badge color change.

**Toast notification** in the dashboard top-right:
```
🚨 QUARANTINE ALERT
Endpoint ep-abc123 has been quarantined.
ERI: 18.3 | Consecutive failures: 14
[View Explainability] [Restore Endpoint]
```

**Persistent alert banner** stays visible while in QUARANTINED state.

**Implementation**: Frontend-only. On WebSocket state update, if state changed to QUARANTINED
or UNSTABLE, push an alert object into a `alerts[]` state array. Render as toast stack.

**Restore button** in the alert: calls `POST /endpoints/{id}/restore` (already implemented
in `relay/api/endpoints.py` per `PLAN_DOCKER_E2E.md`).

### Files
- `relay/dashboard/frontend/src/App.jsx` — alert toast component, `alerts[]` state
- `relay/dashboard/frontend/src/App.css` — toast styles

---

## T12 — Delivery Adapter Interface Formalization (Tier 1 Architecture Gap)
**Closes: `relay/adapters/webhook/` empty stub. Architecture correctness, not demo-visible.**

### Tier 1 Gap being closed
`arch.md` specifies a `DeliveryAdapter` interface that separates the platform core from transport.
The HTTP dispatch is currently embedded directly inside `WebhookWorker`. `relay/adapters/webhook/`
is an empty stub.

### What to build

**`relay/kernel/interfaces/delivery_adapter.py`** (add to existing):
```python
class DeliveryAdapter(Protocol):
    """Transport abstraction. Workers call deliver(); they never know it's HTTP."""
    async def deliver(
        self,
        target_url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> DeliveryResult: ...
```

**`relay/adapters/webhook/adapter.py`** (new):
```python
class WebhookDeliveryAdapter:
    """HTTP Webhook Delivery Adapter. Implements DeliveryAdapter protocol."""
    async def deliver(self, target_url, body, headers, timeout_seconds) -> DeliveryResult:
        # Extract HTTP dispatch from WebhookWorker._post() → move here
```

**`relay/data_plane/workers/webhook.py`** (refactor):
- Remove `_post()` method
- Accept `adapter: DeliveryAdapter` via constructor injection
- Call `adapter.deliver(...)` instead of `_post(...)`

This does not change any behavior — it's a code move + interface formalization.

### Files
- `relay/kernel/interfaces/delivery_adapter.py` — add `DeliveryAdapter` Protocol
- `relay/adapters/webhook/adapter.py` — **NEW** (moved from webhook.py)
- `relay/data_plane/workers/webhook.py` — refactor to use adapter injection
- `relay/data_plane/dispatcher/main.py` — inject `WebhookDeliveryAdapter` into worker

---

## T13 — Typed Internal Event Domain Model (Tier 1 Architecture Gap)
**Closes: `relay/kernel/events/` empty stub. Architecture correctness, not demo-visible.**

### Tier 1 Gap being closed
`arch.md` Section 12 specifies a typed internal event domain model. Kafka topics exist as
string constants but there are no typed Python classes representing the events that flow on them.

### What to build
```
relay/kernel/events/
  __init__.py
  delivery_events.py     # DeliveryAttemptStarted, DeliveryAttemptCompleted, DeliveryFailed
  policy_events.py       # PolicyEvaluationCompleted, PolicyChanged
  endpoint_events.py     # EndpointStateChanged, EndpointQuarantined, EndpointRestored
  replay_events.py       # ReplayRequested, ReplayCompleted
  operational_events.py  # DeadLetterRouted, AlertTriggered
  base.py                # InternalEvent base Pydantic model with correlation_id, causation_id
```

Each event is an immutable Pydantic model with `correlation_id`, `causation_id`, `occurred_at`.
The dispatcher, worker, and AEDE service serialize these instead of raw `json.dumps(dict)`.

### Files
- `relay/kernel/events/base.py` — **NEW**
- `relay/kernel/events/delivery_events.py` — **NEW**
- `relay/kernel/events/policy_events.py` — **NEW**
- `relay/kernel/events/endpoint_events.py` — **NEW**
- `relay/kernel/events/replay_events.py` — **NEW**
- `relay/kernel/events/operational_events.py` — **NEW**

---

## Complete File-by-File Work Summary

| File | Change | Task |
|---|---|---|
| `relay/dashboard/frontend/src/App.jsx` | Major rewrite: tabs, bands, radar, scenarios, DLQ, journal, HMAC header, multi-endpoint, alerts | T1–T6, T8–T9, T11 |
| `relay/dashboard/frontend/src/App.css` | Bands, toast, tabs, radar styles | T1, T6, T11 |
| `relay/api/demo.py` | **NEW** — scenario activation + auto-publisher | T2 |
| `relay/api/main.py` | Add demo router + Prometheus Instrumentator | T2, T7 |
| `relay/api/chart/router.py` | Add `/dlq`, `/benchmark/run` | T4, T5 |
| `relay/api/endpoints.py` | Add `POST /replay` | T5 |
| `relay/data_plane/replay/manager.py` | **NEW** — ReplayManager | T5 |
| `relay/sdk/client.py` | Add `replay_dead_letters()` | T5 |
| `relay/infrastructure/persistence/delivery_attempt_repository.py` | Add `get_dead_letters()` | T5 |
| `relay/dashboard/main.py` | Add `eri_sub_scores` + `state` in WS broadcast | T3 |
| `relay/infrastructure/monitoring/metrics.py` | **NEW** — Prometheus metrics | T7 |
| `relay/data_plane/workers/webhook.py` | Emit metrics; import from auth.signing; inject adapter | T7, T9, T12 |
| `relay/control_plane/aede/service.py` | Emit ERI + state metrics | T7 |
| `relay/infrastructure/auth/signing.py` | **NEW** — signing helpers (refactored from webhook.py) | T9 |
| `relay/infrastructure/auth/__init__.py` | Export signing helpers | T9 |
| `relay/control_plane/aede/synthesizer.py` | Read `RELAY_HMAC_REQUIRED` env flag | T9 |
| `docker-compose.yml` | Add prometheus, grafana, signing env vars | T7, T9 |
| `deploy/prometheus.yml` | **NEW** | T7 |
| `deploy/grafana/provisioning/datasources/prometheus.yml` | **NEW** | T7 |
| `deploy/grafana/dashboards/relay.json` | **NEW** | T7 |
| `deploy/k8s/*.yaml` | **NEW** (~15 files) | T10 |
| `deploy/README-k8s.md` | **NEW** | T10 |
| `simulator/main.py` | All 6 failure modes; signature verification | T2, T9 |
| `relay/kernel/interfaces/delivery_adapter.py` | Add `DeliveryAdapter` Protocol | T12 |
| `relay/adapters/webhook/adapter.py` | **NEW** (moved HTTP from webhook.py) | T12 |
| `relay/data_plane/dispatcher/main.py` | Inject WebhookDeliveryAdapter | T12 |
| `relay/kernel/events/*.py` | **NEW** — 6 typed event model files | T13 |

---

## Revised Sequencing

```
Day 1:      T1  — ERI chart threshold bands (pure frontend, instant visual payoff)
Day 1–2:    T2  — Scenario buttons + auto-publisher (makes everything else testable)
Day 2:      T3  — Radar chart (10-line backend WS change + recharts component)
Day 3:      T5  — ReplayManager + DLQ panel (only substantial backend work)
Day 3–4:    T4  — Benchmark tab (trigger endpoint + grouped bar chart)
Day 4:      T6  — State journal (frontend-only, 1 hour)
Day 4:      T11 — Toast alerts for QUARANTINED (frontend-only, 1 hour)
Day 5:      T9  — HMAC surface (refactor + env wiring + simulator verification)
Day 5–6:    T7  — Prometheus + Grafana (infra wiring)
Day 6–7:    T10 — Kubernetes manifests (~15 YAML files)
Day 7:      T8  — Multi-endpoint view (frontend-only)
Day 8:      T12 — Adapter interface formalization (refactor only, no logic change)
Day 8:      T13 — Typed internal events (domain models, no behavior change)
Buffer:     Full demo script dry run + fix
```

---

## 10-Minute Demo Script (Acceptance Gate)

| Minute | Step | What's visible |
|---|---|---|
| 0:00 | `docker compose up` → open browser → services panel | All 7 services green (Kafka, PG, Redis, API, Dispatcher, Simulator, Dashboard) |
| 1:00 | Click **▶ Normal Operation** | ERI=100, AVAILABLE badge, Timeline shows green ✅ rows, Radar full, Signature header visible |
| 2:00 | Click **⚠ Early Degradation** | ERI line enters yellow band → OBSERVED_DEGRADATION, Policy: "reduced concurrency" |
| 3:30 | Click **🔴 Instability** | ERI enters orange band → UNSTABLE, backoff delay visible in policy panel, alert toast |
| 5:00 | Click **💀 Full Outage** | ERI collapses → 🚨 QUARANTINE ALERT toast → Timeline shows no new HTTP rows → DLQ panel fills |
| 6:30 | Click **🔄 Recovery** | RECOVERING probe → ERI climbs above 90 hysteresis line → AVAILABLE |
| 7:00 | Click **[Replay All]** in DLQ panel | Events requeue, Timeline shows replayed deliveries succeeding |
| 7:30 | Switch to Grafana tab | Real-time ERI gauge, retry histogram, state transition markers |
| 8:00 | Switch to Benchmark tab | Click "Run Scenario 10 — all strategies" → grouped bar chart renders: C wins on all metrics |
| 9:00 | Switch to Multi-Endpoint tab | Show throughput bars: A starved under Strategy A, unaffected under Strategy C |
| 9:30 | Show State Journal panel | Full journey scrollable: AVAILABLE → DEGRADED → UNSTABLE → QUARANTINED → RECOVERING → AVAILABLE |

---

## What Is Still Out of Scope (Unchanged from Original)

- Terraform HCL provisioning
- CLI tool  
- Multi-language SDKs (Java, Node, Go)
- External email/Slack notification service (dashboard toast is the agreed alternative)
- Multi-tenant RBAC
- OpenTelemetry distributed tracing
- Additional delivery adapters (Kafka, RabbitMQ, etc.)
