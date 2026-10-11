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

## 👥 Team Ownership & Role Allocation (Sprint-Aligned)

Following the project governance defined in `docs/execution-plan.md` §C and the sprint plans (`docs/sprints/`):

### Person B — AEDE / Research Core & Lead Full-Stack Engine (Sam)

- **Primary Ownership**:
  - Research Core & Control Plane (AEDE, ERI, State Machine, Explainability)
  - Demo Scenario Engine & Auto-Publisher (`relay/api/demo.py`, `simulator/main.py`) → **T2**
  - Sub-Metric Radar WebSocket stream serialization (`relay/dashboard/main.py`) → **T3**
  - Benchmark Trigger API & metrics streaming (`relay/api/chart/router.py`) → **T4**
  - DLQ ReplayManager backend (`relay/data_plane/replay/manager.py`, SDK, repository) → **T5**
  - Multi-Endpoint noisy-neighbor benchmark evaluation & data extraction → **T8**
  - HMAC Signing surface & auth package refactor (`relay/infrastructure/auth/`) → **T9**
  - Delivery Adapter Protocol definition & worker injection (`relay/adapters/webhook/`) → **T12**
- **Support & Cross-Domain Assistance**:
  - Assists Person A on Prometheus metrics hot paths (**T7**), typed event models (**T13**), and Kubernetes KEDA review (**T10**).
  - Unblocks Person C by establishing exact WebSocket and REST JSON schemas before frontend development begins.

### Person A — Platform Core & Infrastructure
- **Primary Ownership**:
  - Kubernetes production manifests (`deploy/k8s/*.yaml`, `deploy/README-k8s.md`) → **T10**
  - Prometheus metrics registration & Grafana service provisioning (`deploy/prometheus.yml`, `deploy/grafana/`) → **T7**
  - Typed internal event domain models (`relay/kernel/events/`) → **T13**
  - Data plane repository extensions (`delivery_attempt_repository.py`) in collaboration with B → **T5**
- **Support & Review**:
  - Reviews transport adapter extraction (**T12**) and HMAC signing environment configuration (**T9**).

### Person C — Dashboard Frontend & Visualizations (Suhan)
- **Primary Ownership**:
  - All React UI hierarchy & styling (`relay/dashboard/frontend/src/App.jsx`, `App.css`)
  - ERI Chart `ComposedChart` with threshold bands & hysteresis lines → **T1**
  - Demo Scenario preset buttons & active scenario indicator UI → **T2 (UI)**
  - 7-axis `RadarChart` visual component → **T3 (UI)**
  - Benchmark tab grouped bar chart & KPI callout cards → **T4 (UI)**
  - DLQ interactive table & `[Replay All]` / `[Replay Selected]` UI → **T5 (UI)**
  - State Transition Journal component → **T6**
  - Multi-Endpoint side-by-side comparative throughput view → **T8 (UI)**
  - Timeline HMAC signature header badge → **T9 (UI)**
  - Toast alert stack & persistent quarantine banner with restore action → **T11**
- **Boundary**: Strictly frontend implementation; consumes WebSocket and REST APIs provided by B (Sam).

---

## Gap Cross-Reference: What Was Missing vs. What's Now Covered

| Gap (from Architecture Audit) | Tier | Task | Primary Owner | Collaborator / Support |
|---|---|---|---|---|
| ERI chart has no threshold bands | Demo | **T1** | **Person C (Suhan)** | Person B (Sam) |
| No scenario preset buttons | Demo | **T2** | **Person B (Sam)** | Person C (Suhan) |
| No sub-metric radar chart | Demo | **T3** | **Person B (Sam)** (Backend) / **Person C (Suhan)** (UI) | Both |
| No Strategy A/B/C benchmark tab | Demo | **T4** | **Person B (Sam)** (Backend) / **Person C (Suhan)** (UI) | Both |
| `relay/data_plane/replay/` stub & DLQ panel | **Tier 1** | **T5** | **Person B (Sam)** (Backend) / **Person C (Suhan)** (UI) | Person A (Repo review) |
| No live state transition journal | Demo | **T6** | **Person C (Suhan)** | Person B (Sam) |
| Prometheus metrics & Grafana stub | **Tier 1** | **T7** | **Person A** | Person B (Sam) |
| Multi-endpoint noisy-neighbor view | Demo | **T8** | **Person B (Sam)** | Person C (Suhan) |
| HMAC signing surface & auth stub | **Tier 1** | **T9** | **Person B (Sam)** | Person A / Person C (Suhan) |
| `deploy/k8s/` Kubernetes manifests | **Tier 2** | **T10** | **Person A** | Person B (Sam) |
| Quarantine alert notification toasts | **Tier 2** | **T11** | **Person C (Suhan)** | Person B (Sam) |
| Delivery Adapter interface stub | **Tier 1** | **T12** | **Person B (Sam)** | Person A |
| Typed internal event domain models | **Tier 1** | **T13** | **Person A** / **Person B (Sam)** | Both |

---

## Priority Order

```
T1 → T2 → T3 → T5 → T4 → T6 → T9 → T7 → T10 → T11 → T8 → T12 → T13
```

Rationale:
- **T1–T6**: Highest visual return for the live demo. B (Sam) builds backend contracts and hooks, unblocking C (Suhan) immediately.
- **T9**: HMAC signing surface becomes visible in both the dashboard and live HTTP headers.
- **T7**: Prometheus/Grafana closes the Tier 1 monitoring stub.
- **T10**: Kubernetes manifests close the MVP checklist requirement.
- **T11**: Alert notifications visible in demo with low implementation cost.
- **T12–T13**: Architecture correctness; needed to fulfill full `arch.md` claims.

---

## T1 — ERI Chart with Threshold Bands & State Color Coding
- **Primary Owner**: **Person C (Suhan)** (Frontend UI & Recharts)
- **Support**: **Person B (Sam)** (Data contract validation)
- **Demo impact**: Highest. No backend changes required. Pure frontend.

### What to build
Replace the plain `LineChart` with a `ComposedChart`:
- **Colored `ReferenceArea` bands** pinned to ERI thresholds:
  - 0–30 → `#ef444422` Red (QUARANTINED zone)
  - 30–60 → `#f9731622` Orange (UNSTABLE zone)
  - 60–80 → `#eab30822` Yellow (OBSERVED_DEGRADATION zone)
  - 80–90 → `#86efac22` Light green (RECOVERING zone)
  - 90–100 → `#22c55e22` Bright green (AVAILABLE zone)
- **Two `ReferenceLine`s**: degradation at 80 (yellow dashed), recovery at 90 (green dashed) — shows hysteresis gap visually.
- **Dynamic ERI line color**: changes dynamically to match current endpoint state.
- **State transition dots**: colored markers on the line where state transitions occurred.
- **Three sparkline sub-charts** below the main ERI line: `success_rate`, `timeout_rate`, `consecutive_failures`.

### Division of Work
- **Person B (Sam)**: Verify that `relay/dashboard/main.py`'s WebSocket stream includes `"state"` inside each `eri_history` item.
- **Person C (Suhan)**: Implement `ComposedChart`, `ReferenceArea`, `ReferenceLine`, and CSS styles in `App.jsx` and `App.css`.

### Files
- `relay/dashboard/frontend/src/App.jsx` — replace `LineChart` with `ComposedChart` + bands
- `relay/dashboard/frontend/src/App.css` — band label & chart styles

---

## T2 — Scenario Preset Buttons (The One-Click Demo Panel)
- **Primary Owner**: **Person B (Sam)** (Backend API, Simulator wiring & auto-publisher loop)
- **Collaborator**: **Person C (Suhan)** (Frontend control buttons & active scenario banner)
- **Demo impact**: Critical. Eliminates all terminal interaction during the demo.

### What to build
A prominent **"Demo Scenarios" control panel** with large labeled buttons. Each button:
1. Calls `POST /demo/scenario` on the backend to configure the simulator.
2. Starts auto-publishing events at 2/second to keep the pipeline flowing.
3. Highlights the active scenario name in a persistent dashboard banner.

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

### Division of Work
- **Person B (Sam)**:
  - Create `relay/api/demo.py` with `POST /demo/scenario` (configures simulator, launches background event publishing task).
  - Include demo router in `relay/api/main.py`.
  - Complete all 6 failure modes in `simulator/main.py` (rate_limit 429, auth_failure 401/403, connection_reset).
- **Person C (Suhan)**:
  - Add scenario button grid to `relay/dashboard/frontend/src/App.jsx`.
  - Wire button click handlers to `fetch('/demo/scenario')`.
  - Add active scenario badge banner.

### Files
- `relay/api/demo.py` — **NEW** (Person B)
- `relay/api/main.py` — include demo router (Person B)
- `simulator/main.py` — complete all 6 failure modes (Person B)
- `relay/dashboard/frontend/src/App.jsx` — scenario buttons UI (Person C)

---

## T3 — Sub-Metric Radar Chart (The "Why" Visualizer)
- **Primary Owner**: **Person B (Sam)** (Backend WS serialization) & **Person C (Suhan)** (Frontend Radar component)
- **Demo impact**: High. Shows WHICH specific signal triggered the policy adaptation.

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

Each axis shrinks toward center as that sub-metric degrades. During timeout faults, the `timeout` axis collapses while `success_rate` remains high.

### Division of Work
- **Person B (Sam)**:
  - Modify `relay/dashboard/main.py` WebSocket broadcast to include `eri_sub_scores` dict inside the state update payload.
- **Person C (Suhan)**:
  - Add Recharts `RadarChart`, `PolarGrid`, `PolarAngleAxis`, and `Radar` components in `App.jsx`.
  - Bind radar axes to live WebSocket `eri_sub_scores` values.

### Files
- `relay/dashboard/main.py` — add `eri_sub_scores` to WS broadcast (Person B)
- `relay/dashboard/frontend/src/App.jsx` — `RadarChart` UI component (Person C)

---

## T4 — Strategy A/B/C Benchmark Tab
- **Primary Owner**: **Person B (Sam)** (API trigger & metrics calculation)
- **Collaborator**: **Person C (Suhan)** (Benchmark UI & Grouped Bar Chart)
- **Demo impact**: High. Quantitative research claim visualizer.

### What to build
A dedicated **"Benchmark" tab** in the dashboard with:
1. **Trigger buttons** per strategy: `[▶ Run Scenario 10 — Strategy A]` `[▶ B]` `[▶ C (AEDE)]`
2. **Grouped bar chart** (3 bars per scenario):
   - X-axis: held-out scenarios (3, 4, 5, 9, 10, 12, 13, 14)
   - Y-axis: selectable metric (retry count / success rate / MTTR / wasted retries)
   - Colors: Strategy A = red, B = blue, C = green
3. **KPI callout cards**:
   ```
   C vs A                C vs B
   ↓73% retries          ↓41% retries
   ↑28% success rate     ↑12% success rate
   ↓65% MTTR             ↓22% MTTR
   ```

### Division of Work
- **Person B (Sam)**:
  - Add `POST /benchmark/run` in `relay/api/chart/router.py` to invoke `benchmark/runner.py` in a background thread and populate `benchmark_store`.
  - Format scenario metric comparison JSON payloads.
- **Person C (Suhan)**:
  - Implement Benchmark Tab, strategy run buttons, grouped bar chart, and KPI stat cards in `App.jsx`.

### Files
- `relay/api/chart/router.py` — add `POST /benchmark/run` (Person B)
- `relay/dashboard/frontend/src/App.jsx` — Benchmark tab with GroupedBarChart (Person C)

---

## T5 — ReplayManager + DLQ Panel (Full Event Lifecycle)
- **Primary Owner**: **Person B (Sam)** (Full backend slice: ReplayManager, API, SDK, Repo)
- **Collaborator**: **Person C (Suhan)** (Frontend DLQ table & replay actions)
- **Support**: **Person A** (Repository architecture review)
- **Demo impact**: High. Closes Tier 1 gap and MVP checklist requirement.

### Tier 1 Gap being closed
`relay/data_plane/replay/` is an empty stub. `replay_dead_letters()` does not exist in SDK.
The MVP checklist explicitly requires "Replay from DLQ works without mutating history".

### Division of Work
- **Person B (Sam)**:
  - Create `relay/data_plane/replay/manager.py` implementing `ReplayManager.replay_dead_letters()`: queries `DEAD_LETTER` attempts, generates new immutable `DeliveryAttempt` with attempt #1, republishes to `events.business`.
  - Add `POST /replay` in `relay/api/endpoints.py`.
  - Add `GET /dlq` in `relay/api/chart/router.py`.
  - Add `get_dead_letters()` in `relay/infrastructure/persistence/delivery_attempt_repository.py`.
  - Add `replay_dead_letters()` to `relay/sdk/client.py`.
- **Person C (Suhan)**:
  - Build interactive DLQ panel in `relay/dashboard/frontend/src/App.jsx`.
  - Display dead-lettered events table with columns: Event ID, Type, Endpoint, Attempt #, Failed At.
  - Wire `[Replay All]` and `[Replay Selected]` buttons to `POST /replay`.

### Files
- `relay/data_plane/replay/manager.py` — **NEW** (Person B)
- `relay/api/endpoints.py` — add `POST /replay` (Person B)
- `relay/api/chart/router.py` — add `GET /dlq` (Person B)
- `relay/sdk/client.py` — add `replay_dead_letters()` (Person B)
- `relay/infrastructure/persistence/delivery_attempt_repository.py` — add `get_dead_letters()` (Person B)
- `relay/dashboard/frontend/src/App.jsx` — DLQ table and replay triggers (Person C)

---

## T6 — Live State Transition Journal
- **Primary Owner**: **Person C (Suhan)** (Frontend state history tracking & UI)
- **Support**: **Person B (Sam)** (Event payload schema)
- **Demo impact**: Medium-High. Makes the entire state transition lifecycle readable in chronological order. Frontend-only.

### What to build
A **"State History" panel** showing:
```
🟢 23:12:04  AVAILABLE
⚠  23:12:31  AVAILABLE → OBSERVED_DEGRADATION   [ERI: 74.2 | success_rate fell to 61%]
🔴 23:12:52  OBSERVED_DEGRADED → UNSTABLE        [ERI: 44.8 | 8 consecutive failures]
💀 23:13:01  UNSTABLE → QUARANTINED              [ERI: 18.3 | quarantine threshold crossed]
🔄 23:14:30  QUARANTINED → RECOVERING            [admin restore triggered]
🟢 23:14:58  RECOVERING → AVAILABLE              [ERI: 91.2 | above recovery threshold (90)]
```

### Division of Work
- **Person C (Suhan)**: Track previous vs new state in React state on each WebSocket message; append an entry with timestamp, transition direction, ERI score, and triggered thresholds.
- **Person B (Sam)**: Verify that `triggered_thresholds` and `reason` strings are emitted in the WebSocket feed.

### Files
- `relay/dashboard/frontend/src/App.jsx` — State Journal component (Person C)

---

## T7 — Prometheus Metrics + Grafana Dashboard (Tier 1 Monitoring Gap)
- **Primary Owner**: **Person A** (Prometheus & Grafana infrastructure)
- **Collaborator**: **Person B (Sam)** (Instrumenting AEDEService & WebhookWorker hot paths)
- **Closes**: `relay/infrastructure/monitoring/` empty stub. MVP checklist requirement.

### What to build
1. **Metrics module** (`relay/infrastructure/monitoring/metrics.py`):
   - `DELIVERY_ATTEMPTS`: Counter with labels `[endpoint_id, status]`
   - `ERI_SCORE`: Gauge with label `[endpoint_id]`
   - `ENDPOINT_STATE`: Gauge with labels `[endpoint_id, from_state, to_state]`
   - `RETRY_COUNT`: Histogram with exponential buckets
2. **FastAPI instrumentation**: `/metrics` exposed via `prometheus_fastapi_instrumentator`.
3. **Docker Compose**: Add `prometheus:v2.51.0` and `grafana:10.4.0` containers.
4. **Provisioned Grafana dashboard**: `deploy/grafana/dashboards/relay.json`.

### Division of Work
- **Person A**:
  - Implement `relay/infrastructure/monitoring/metrics.py`.
  - Add Prometheus `Instrumentator` to `relay/api/main.py`.
  - Create `deploy/prometheus.yml` and `deploy/grafana/provisioning/datasources/prometheus.yml`.
  - Update `docker-compose.yml` with prometheus and grafana services.
- **Person B (Sam)**:
  - Wire metric emissions into `relay/data_plane/workers/webhook.py` (attempts, retry histograms).
  - Wire metric emissions into `relay/control_plane/aede/service.py` (ERI gauge, state transitions).
  - Construct `deploy/grafana/dashboards/relay.json` with live gauges, retry histograms, and state event markers.

### Files
- `relay/infrastructure/monitoring/metrics.py` — **NEW** (Person A)
- `relay/api/main.py` — add Instrumentator (Person A)
- `deploy/prometheus.yml` — **NEW** (Person A)
- `deploy/grafana/provisioning/datasources/prometheus.yml` — **NEW** (Person A)
- `docker-compose.yml` — add prometheus and grafana services (Person A)
- `relay/data_plane/workers/webhook.py` — emit metrics (Person B)
- `relay/control_plane/aede/service.py` — emit metrics (Person B)
- `deploy/grafana/dashboards/relay.json` — **NEW** (Person B)

---

## T8 — Multi-Endpoint View (Noisy-Neighbor Proof)
- **Primary Owner**: **Person B (Sam)** (Benchmark data generation & extraction)
- **Collaborator**: **Person C (Suhan)** (Side-by-side comparative UI)
- **Demo impact**: Medium. Proves the noisy-neighbor isolation property visually.

### What to build
A **"Multi-Endpoint" tab** in the dashboard comparing:
- Left: Endpoint A (failing endpoint under quarantine)
- Right: Endpoint B (healthy neighbor endpoint)
- Visualizing how Strategy A starves Endpoint B due to worker exhaustion, while Strategy C (AEDE) isolates A and preserves 100% throughput for B.

### Division of Work
- **Person B (Sam)**: Extract results from `benchmark/multi_endpoint.py` into chart store format.
- **Person C (Suhan)**: Build side-by-side throughput bars and isolation badges in `App.jsx`.

### Files
- `relay/dashboard/frontend/src/App.jsx` — Multi-Endpoint tab component (Person C)

---

## T9 — HMAC Signing Surface (Tier 1 Auth Gap)
- **Primary Owner**: **Person B (Sam)** (Auth refactor, policy synthesis & simulator verification)
- **Support**: **Person A** (Docker Compose environment) & **Person C (Suhan)** (UI display)
- **Closes**: `relay/infrastructure/auth/` empty stub.

### What to build
1. Move existing signing logic to `relay/infrastructure/auth/signing.py` (`compute_relay_signature`, `verify_relay_signature`).
2. Wire `RELAY_WEBHOOK_SIGNING_SECRET` and `RELAY_HMAC_REQUIRED=true` in `docker-compose.yml`.
3. Policy Synthesizer sets `hmac_required=True` in `IntegrityPolicy`.
4. Simulator webhook receiver verifies incoming signatures and logs `"SIGNATURE VERIFIED"`.
5. Dashboard timeline displays `X-Relay-Signature: sha256=...` badge.

### Division of Work
- **Person B (Sam)**:
  - Create `relay/infrastructure/auth/signing.py` and `relay/infrastructure/auth/__init__.py`.
  - Refactor `relay/data_plane/workers/webhook.py` to import from `auth.signing`.
  - Update `relay/control_plane/aede/synthesizer.py` to read `RELAY_HMAC_REQUIRED`.
  - Update `simulator/main.py` to verify signatures and log verification.
- **Person A**: Add signing secret env vars to `docker-compose.yml`.
- **Person C (Suhan)**: Display signature badge in `App.jsx` timeline rows.

### Files
- `relay/infrastructure/auth/signing.py` — **NEW** (Person B)
- `relay/infrastructure/auth/__init__.py` — export signing helpers (Person B)
- `relay/data_plane/workers/webhook.py` — refactor import (Person B)
- `relay/control_plane/aede/synthesizer.py` — read HMAC flag (Person B)
- `simulator/main.py` — signature verification hook (Person B)
- `docker-compose.yml` — signing env vars (Person A)
- `relay/dashboard/frontend/src/App.jsx` — show signature header (Person C)

---

## T10 — Kubernetes Production Manifests (Tier 2 / MVP Checklist Item)
- **Primary Owner**: **Person A** (Manifest authoring & documentation)
- **Support**: **Person B (Sam)** (KEDA autoscaling review & probe parameters)
- **Closes**: `deploy/k8s/` does not exist. Closes mandatory MVP checklist requirement.

### What to build
Create complete `deploy/k8s/` tree:
```
deploy/
  k8s/
    namespace.yaml
    configmap.yaml
    secret.yaml
    kafka/statefulset.yaml
    postgres/statefulset.yaml
    redis/deployment.yaml
    relay-api/deployment.yaml, service.yaml, ingress.yaml, hpa.yaml
    relay-dispatcher/deployment.yaml, hpa.yaml
    relay-dashboard/deployment.yaml, service.yaml
    relay-simulator/deployment.yaml, service.yaml
    prometheus/deployment.yaml
    grafana/deployment.yaml
  README-k8s.md
```

### Division of Work
- **Person A**: Write all ~15 Kubernetes manifests and deployment verification guide in `deploy/README-k8s.md`.
- **Person B (Sam)**: Review HPA consumer lag metrics (KEDA triggers for Dispatcher) and liveness/readiness probes.

### Files
- `deploy/k8s/*.yaml` — **ALL NEW** (~15 files) (Person A)
- `deploy/README-k8s.md` — **NEW** (Person A)

---

## T11 — Quarantine Alert Notifications (Tier 2 / Dashboard Alert Panel)
- **Primary Owner**: **Person C (Suhan)** (Toast notification UI & persistent alert banner)
- **Support**: **Person B (Sam)** (Verification of `/endpoints/{id}/restore` endpoint)
- **Closes**: Dashboard operational alert panel requirement.

### What to build
- Top-right toast notification on transition to `QUARANTINED` or `UNSTABLE`:
  ```
  🚨 QUARANTINE ALERT
  Endpoint ep-abc123 has been quarantined.
  ERI: 18.3 | Consecutive failures: 14
  [View Explainability] [Restore Endpoint]
  ```
- Persistent alert banner stays visible while in quarantine.
- `[Restore Endpoint]` button calls `POST /endpoints/{id}/restore`.

### Division of Work
- **Person C (Suhan)**: Build `alerts[]` state, toast notification stack, banner, and restore action hook in `App.jsx` and `App.css`.
- **Person B (Sam)**: Ensure backend `POST /endpoints/{id}/restore` publishes state transition event that clears the alert.

### Files
- `relay/dashboard/frontend/src/App.jsx` — alert toast & banner components (Person C)
- `relay/dashboard/frontend/src/App.css` — toast styles (Person C)

---

## T12 — Delivery Adapter Interface Formalization (Tier 1 Architecture Gap)
- **Primary Owner**: **Person B (Sam)** (Protocol formalization & worker refactor)
- **Support**: **Person A** (Dispatcher injection review)
- **Closes**: `relay/adapters/webhook/` empty stub. Architecture correctness.

### What to build
1. Formalize `DeliveryAdapter` protocol in `relay/kernel/interfaces/delivery_adapter.py`.
2. Extract HTTP dispatch from `WebhookWorker._post()` into `relay/adapters/webhook/adapter.py`.
3. Inject `DeliveryAdapter` into `WebhookWorker` constructor.

### Division of Work
- **Person B (Sam)**:
  - Add `DeliveryAdapter` Protocol to `relay/kernel/interfaces/delivery_adapter.py`.
  - Create `WebhookDeliveryAdapter` in `relay/adapters/webhook/adapter.py`.
  - Refactor `relay/data_plane/workers/webhook.py` to use injected adapter.
  - Update `relay/data_plane/dispatcher/main.py` to construct and inject the adapter.
- **Person A**: Review interface separation.

### Files
- `relay/kernel/interfaces/delivery_adapter.py` — add Protocol (Person B)
- `relay/adapters/webhook/adapter.py` — **NEW** (Person B)
- `relay/data_plane/workers/webhook.py` — inject adapter (Person B)
- `relay/data_plane/dispatcher/main.py` — wire adapter (Person B)

---

## T13 — Typed Internal Event Domain Model (Tier 1 Architecture Gap)
- **Primary Owner**: **Person A** & **Person B (Sam)** (Domain model definition)
- **Closes**: `relay/kernel/events/` empty stub.

### What to build
Create immutable typed internal event models in `relay/kernel/events/`:
```
relay/kernel/events/
  __init__.py
  base.py                # InternalEvent base Pydantic model (correlation_id, causation_id, occurred_at)
  delivery_events.py     # DeliveryAttemptStarted, DeliveryAttemptCompleted, DeliveryFailed
  policy_events.py       # PolicyEvaluationCompleted, PolicyChanged
  endpoint_events.py     # EndpointStateChanged, EndpointQuarantined, EndpointRestored
  replay_events.py       # ReplayRequested, ReplayCompleted
  operational_events.py  # DeadLetterRouted, AlertTriggered
```

### Division of Work
- **Person A**: Author `base.py`, `delivery_events.py`, and `operational_events.py`.
- **Person B (Sam)**: Author `policy_events.py`, `endpoint_events.py`, and `replay_events.py`.

### Files
- `relay/kernel/events/*.py` — **NEW** (6 files) (Person A & Person B)

---

## Complete File-by-File Work Summary

| File | Change | Task | Primary Owner |
|---|---|---|---|
| `relay/dashboard/frontend/src/App.jsx` | Major rewrite: tabs, bands, radar, scenarios, DLQ, journal, HMAC header, multi-endpoint, alerts | T1–T6, T8–T9, T11 | **Person C (Suhan)** |
| `relay/dashboard/frontend/src/App.css` | Bands, toast, tabs, radar styles | T1, T6, T11 | **Person C (Suhan)** |
| `relay/api/demo.py` | **NEW** — scenario activation + auto-publisher | T2 | **Person B (Sam)** |
| `relay/api/main.py` | Add demo router + Prometheus Instrumentator | T2, T7 | **Person B (Sam)** & **Person A** |
| `relay/api/chart/router.py` | Add `/dlq`, `/benchmark/run` | T4, T5 | **Person B (Sam)** |
| `relay/api/endpoints.py` | Add `POST /replay` | T5 | **Person B (Sam)** |
| `relay/data_plane/replay/manager.py` | **NEW** — ReplayManager | T5 | **Person B (Sam)** |
| `relay/sdk/client.py` | Add `replay_dead_letters()` | T5 | **Person B (Sam)** |
| `relay/infrastructure/persistence/delivery_attempt_repository.py` | Add `get_dead_letters()` | T5 | **Person B (Sam)** |
| `relay/dashboard/main.py` | Add `eri_sub_scores` + `state` in WS broadcast | T3 | **Person B (Sam)** |
| `relay/infrastructure/monitoring/metrics.py` | **NEW** — Prometheus metrics | T7 | **Person A** |
| `relay/data_plane/workers/webhook.py` | Emit metrics; import auth.signing; inject adapter | T7, T9, T12 | **Person B (Sam)** |
| `relay/control_plane/aede/service.py` | Emit ERI + state metrics | T7 | **Person B (Sam)** |
| `relay/infrastructure/auth/signing.py` | **NEW** — signing helpers | T9 | **Person B (Sam)** |
| `relay/infrastructure/auth/__init__.py` | Export signing helpers | T9 | **Person B (Sam)** |
| `relay/control_plane/aede/synthesizer.py` | Read `RELAY_HMAC_REQUIRED` env flag | T9 | **Person B (Sam)** |
| `docker-compose.yml` | Add prometheus, grafana, signing env vars | T7, T9 | **Person A** |
| `deploy/prometheus.yml` | **NEW** | T7 | **Person A** |
| `deploy/grafana/provisioning/datasources/prometheus.yml` | **NEW** | T7 | **Person A** |
| `deploy/grafana/dashboards/relay.json` | **NEW** | T7 | **Person B (Sam)** |
| `deploy/k8s/*.yaml` | **NEW** (~15 files) | T10 | **Person A** |
| `deploy/README-k8s.md` | **NEW** | T10 | **Person A** |
| `simulator/main.py` | All 6 failure modes; signature verification | T2, T9 | **Person B (Sam)** |
| `relay/kernel/interfaces/delivery_adapter.py` | Add `DeliveryAdapter` Protocol | T12 | **Person B (Sam)** |
| `relay/adapters/webhook/adapter.py` | **NEW** (moved HTTP from webhook.py) | T12 | **Person B (Sam)** |
| `relay/data_plane/dispatcher/main.py` | Inject WebhookDeliveryAdapter | T12 | **Person B (Sam)** |
| `relay/kernel/events/*.py` | **NEW** — 6 typed event model files | T13 | **Person A** & **Person B (Sam)** |

---

## 📅 Revised Sequencing (Multi-Track Sprint Schedule)

```
        PERSON B (Sam)                PERSON C (Suhan)              PERSON A
        [Cross-Domain Engine]         [Dashboard Frontend]          [Infra & Core]
───────────────────────────────────────────────────────────────────────────────────────
Day 1:  • Verify WS payload contract   • T1: ComposedChart with     • T13: Draft typed event
          for ERI & state               bands & hysteresis lines      base models in kernel
        • T2: Build relay/api/demo.py
          & simulator failure modes

Day 2:  • T3: Serialize eri_sub_scores • T2: Build Scenario Preset  • T7: Implement
          into WebSocket stream         buttons in App.jsx            monitoring/metrics.py
        • T5: Build ReplayManager &    • T3: Implement Recharts       & Prometheus scrape
          POST /replay, GET /dlq        RadarChart                    config

Day 3:  • T4: Add /benchmark/run       • T5: Build DLQ table &      • T7: Configure
          endpoint in chart router      replay action buttons         Prometheus & Grafana
        • T9: Extract auth/signing.py  • T4: Build Benchmark tab      in docker-compose.yml
          & wire HMAC policy flag       grouped bar chart UI

Day 4:  • T12: Formalize               • T6: Implement State        • T10: Begin Kubernetes
          DeliveryAdapter interface     Transition Journal            manifests (namespace,
        • Verify /restore hook for     • T11: Build Quarantine        configmap, secrets,
          alert dismissals              toast alerts & banner         statefulsets)

Day 5:  • T7: Wire metrics in worker   • T8: Build Multi-Endpoint   • T10: Write API,
          & AEDEService; construct      comparative view              Dispatcher & Dashboard
          relay.json Grafana dash      • T9: Render HMAC signature    deployments + services
                                        header badge in timeline

Day 6:  • T8: Format multi-endpoint    • UI Polish: responsive      • T10: Configure KEDA
          contention benchmark data     tabs, consistent styling,     autoscalers & write
        • T13: Complete policy &        animations                    deploy/README-k8s.md
          endpoint event models

Day 7:  • Full stack verification      • Rehearsal: dry run of      • Validate K8s manifests
          across Docker Compose         10-minute demo script         syntax & linting

Day 8:  • Buffer: End-to-end rehearsal, edge-case fixes, and timing optimization with all three
```

---

## 10-Minute Demo Script (Acceptance Gate)

| Minute | Step | Role Executing | What's visible |
|---|---|---|---|
| 0:00 | `docker compose up` → open browser → services panel | **Person A** | All 7 services green (Kafka, PG, Redis, API, Dispatcher, Simulator, Dashboard, Prometheus, Grafana) |
| 1:00 | Click **▶ Normal Operation** | **Person C** | ERI=100, AVAILABLE badge, Timeline shows green ✅ rows, Radar full, Signature header visible |
| 2:00 | Click **⚠ Early Degradation** | **Person C** | ERI line enters yellow band → OBSERVED_DEGRADATION, Policy: "reduced concurrency" |
| 3:30 | Click **🔴 Instability** | **Person C** | ERI enters orange band → UNSTABLE, backoff delay visible in policy panel, alert toast |
| 5:00 | Click **💀 Full Outage** | **Person C** | ERI collapses → 🚨 QUARANTINE ALERT toast → Timeline shows no new HTTP rows → DLQ panel fills |
| 6:30 | Click **🔄 Recovery** | **Person C** | RECOVERING probe → ERI climbs above 90 hysteresis line → AVAILABLE |
| 7:00 | Click **[Replay All]** in DLQ panel | **Person B** | Events requeue, Timeline shows replayed deliveries succeeding |
| 7:30 | Switch to Grafana tab | **Person A** | Real-time ERI gauge, retry histogram, state transition markers |
| 8:00 | Switch to Benchmark tab | **Person B** | Click "Run Scenario 10 — all strategies" → grouped bar chart renders: C wins on all metrics |
| 9:00 | Switch to Multi-Endpoint tab | **Person B** | Show throughput bars: A starved under Strategy A, unaffected under Strategy C |
| 9:30 | Show State Journal panel | **Person C** | Full journey scrollable: AVAILABLE → DEGRADED → UNSTABLE → QUARANTINED → RECOVERING → AVAILABLE |

---

## What Is Still Out of Scope (Unchanged from Original)

- Terraform HCL provisioning
- CLI tool  
- Multi-language SDKs (Java, Node, Go)
- External email/Slack notification service (dashboard toast is the agreed alternative)
- Multi-tenant RBAC
- OpenTelemetry distributed tracing
- Additional delivery adapters (Kafka, RabbitMQ, etc.)
