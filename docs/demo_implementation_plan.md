# RelayForge — Complete Implementation Plan (Demo + Architecture Gaps)

> **Goal**: Make the platform visually demonstrable in a live academic setting AND close all
> Tier 1 architecture gaps so the codebase matches the claims in `arch.md`.
> A person who has never seen the project must understand what is happening within 5 seconds
> of watching each panel. Stubs must be code, not `.gitkeep`.
>
> **Revision (2026-10-12)**: status tables and task specs corrected after a code audit of
> `feature/research-contribution`. Corrections are marked **[Audit]**. Person B's detailed
> build plan, API contracts and open decisions are in `docs/sam_implementation_plan.md`.
>
> **Revision 2 (2026-10-12)**: Person B's backend work is built on `feature/demo-backend` and
> verified on the compose stack. Status changes are marked **[Built]**. The task sections below
> keep their original wording as the specification; where the build differs, a **[Built]** note
> says how.

---

## Task Status

| Task | Backend (Person B / A) | Frontend (Person C) |
|---|---|---|
| T1 ERI chart bands | ✅ `thresholds`, `state` and `sub_scores` are in the live payload | ❌ Not started |
| T2 Scenario presets | ✅ 8 presets, simulator modes, auto-publisher | ❌ Not started |
| T3 Radar chart | ✅ `/ws` streams live AEDE data | ❌ Not started |
| T4 Benchmark tab | ✅ `POST /api/chart/benchmark/run` with computed `comparison` | ❌ Not started |
| T5 Replay + DLQ | ✅ ReplayManager, `POST /replay`, `GET /api/chart/dlq`, SDK (ADR 0021) | ❌ Not started |
| T6 State journal | ✅ `reason` and `triggered_thresholds` are in the payload | ❌ Not started |
| T7 Prometheus + Grafana | ❌ Not started (Person A's metrics module comes first) | — |
| T8 Multi-endpoint | ✅ `GET /api/chart/benchmark/multi-endpoint` | ❌ Not started |
| T9 HMAC surface | ✅ Signing on in compose; simulator verifies and records | ❌ Not started |
| T10 Kubernetes | ❌ Not started (Person A) | — |
| T11 Quarantine alerts | ✅ Restore fixed (ADR 0020) | ❌ Not started |
| T12 Delivery adapter | ✅ Done | — |
| T13 Typed events | ❌ Not started (Person A's `base.py` comes first) | — |
| T14 Repeat endpoint registration (optional) | ❌ Open question for Person A. Not a bug fix; nothing depends on it | — |

Still open and unassigned: explainability records and the policy cache are in memory only; the
frontend WebSocket URL points at port 8000 instead of 8090; nothing in compose serves the React
build. ADR 0020 and ADR 0021 are Proposed and need Person B's sign-off.

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
| Explainability records — every policy decision recorded with thresholds, metrics, reason text | `relay/control_plane/explainability/record.py` | ⚠️ Records generated; **[Audit]** stored in memory only (`explainability_repo.py` has no Postgres implementation). Unassigned gap |
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
| HMAC-SHA256 payload signing — code exists, activated by `hmac_required` in policy | `relay/data_plane/workers/webhook.py` | ✅ Done. **[Built]** Helpers moved to `relay/infrastructure/auth/signing.py`; signing is on in docker-compose (T9) |
| SSRF guard (blocks private IP targets in production) | `relay/infrastructure/url_guard.py` | ✅ Done |
| Retry Manager (schedules future attempts from policy intervals) | `relay/data_plane/retry_manager/manager.py` | ✅ Done |
| Dead Letter Queue routing | dispatcher + retry manager | ⚠️ **[Audit]** Exhausted deliveries are marked `FAILED`; `DEAD_LETTER` is only set for a missing/inactive endpoint; deliveries skipped under PAUSE/QUARANTINE stay `PENDING` and are never re-dispatched → **T5**. **[Built]** Statuses are unchanged; the DLQ view covers all three cases and replay re-dispatches them (ADR 0021) |
| Three-tier policy fallback (AEDE live → Redis cache → Strategy A static defaults) | `relay/infrastructure/cache.py` + dispatcher | ✅ Done |
| Policy cache with TTL | `relay/infrastructure/cache.py` | ⚠️ **[Audit]** In-memory implementation only; the dispatcher runs on `InMemoryPolicyCache`. No Redis-backed class exists. Unassigned gap |
| Feature flags (`AEDE_ENABLED`, `ERI_ENABLED`, `STATE_MACHINE_ENABLED`, `STATIC_RETRY_MODE`, `ADAPTIVE_RETRY_MODE`) | `relay/infrastructure/feature_flags.py` | ✅ Done |
| PostgreSQL repositories (Events, Endpoints, Subscriptions, Deliveries, Attempts, Telemetry, Explainability) | `relay/infrastructure/persistence/` | ✅ Done |
| Alembic DB migrations | `alembic.ini` + `relay/infrastructure/persistence/migrations/` | ✅ Done |

### Infrastructure & Deployment (partially complete)
| Component | Location | Status |
|---|---|---|
| Docker Compose stack (Kafka, PostgreSQL, Redis, API, Dispatcher, Simulator, Dashboard) | `docker-compose.yml` | ✅ Done |
| Multi-stage Dockerfile | `Dockerfile` | ✅ Done |
| Endpoint Simulator | `simulator/` | ⚠️ **[Audit]** 4 modes exist (http_error, timeout, slow, rate_limit). `success`, `mixed`, `auth_failure`, `connection_reset` are missing → **T2**. **[Built]** All 8 modes exist |
| Prometheus & Grafana | — | ❌ Not in docker-compose → **T7** |
| Kubernetes manifests | — | ❌ `deploy/k8s/` does not exist → **T10** |

### Dashboard (partially complete)
| Component | Location | Status |
|---|---|---|
| WebSocket backend streaming state updates | `relay/dashboard/websocket.py` | ⚠️ **[Audit]** Stream is synthetic (`DashboardDemoSource`), not fed by the real pipeline; `delivery_events` is always empty → **T3**. **[Built]** `/ws` reads the live `state_store` and Postgres; the synthetic source runs only with `DASHBOARD_SYNTHETIC_SOURCE=true` |
| Chart Data API (endpoints, ERI history, policy, explainability, benchmark results) | `relay/api/chart/router.py` | ✅ Done, except **[Audit]** benchmark results are always empty: the runner writes to a different store than the router reads → **T4**. **[Built]** Stores bridged; DLQ, benchmark-run and multi-endpoint routes added |
| React frontend — endpoint state badge, ERI line chart, delivery timeline, policy panel, explainability panel, fault injection controls | `relay/dashboard/frontend/src/App.jsx` | ✅ Basic done. **[Audit]** WebSocket URL points at port 8000 (API); the dashboard is on 8090. Nothing in compose serves the React build. **[Built]** CORS is now on the API, dashboard and simulator (`RELAY_CORS_ORIGINS`); the port and the missing frontend service are still open |
| ERI chart threshold bands + hysteresis lines | — | ❌ UI → **T1** (data ready) |
| Scenario preset buttons | — | ❌ UI → **T2** (API ready) |
| Sub-metric radar chart | — | ❌ UI → **T3** (data ready) |
| Strategy A/B/C benchmark comparison tab | — | ❌ UI → **T4** (API ready) |
| DLQ panel + replay button | — | ❌ UI → **T5** (API ready) |
| State transition journal | — | ❌ UI → **T6** (data ready) |
| Quarantine alert toasts | — | ❌ UI → **T11** (restore fixed) |

### SDK & Domain Model (complete)
| Component | Location | Status |
|---|---|---|
| Python SDK (`publish_event`, `register_endpoint`, `create_subscription`, `get_event`) | `relay/sdk/client.py` | ✅ Done |
| `replay_dead_letters()` SDK method | `relay/sdk/client.py` | ✅ **[Built]** (T5) |
| Core domain entities (Event, Endpoint, Subscription, Delivery, DeliveryAttempt, DeliveryPolicy, ERIScore, EndpointState, Telemetry, ExplainabilityRecord, PolicyEvaluation) | `relay/kernel/` | ✅ Done |
| Typed internal event domain model | `relay/kernel/events/` | ❌ Empty stub → **T13** |

### Tests (436/436 passing on `feature/demo-backend`)
| Suite | Count | Status |
|---|---|---|
| Unit, data-plane, dashboard and simulator tests | 415 | ✅ All passing |
| Integration tests (`tests/integration/`) | 21 | ✅ All passing (including live Kafka pipeline, e2e demo flow, telemetry loop) |
| Total | **436** | ✅ Exit code 0 (2026-10-12, Kafka running) |

**[Built]** Three test modules need a running Kafka broker and hang without one
(`test_kafka_pipeline.py`, `test_telemetry_loop.py`, `test_benchmark.py::test_validate_against_real_aede`).
`tests/integration/test_end_to_end_adaptation.py` fails intermittently: it uses an unseeded
random failure rate.

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
  - Decision on what `POST /endpoints` does when an owner registers the same URL twice (optional) → **T14**
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
| `POST /endpoints` accepts the same URL twice for one owner (found in review) | Optional | **T14** | **Person A** | Person B (Sam) |

---

## Priority Order

```
T1 → T2 → T3 → T5 → T4 → T6 → T9 → T7 → T10 → T11 → T8 → T12 → T13 (T14 is optional)
```

**[Audit]** Person B builds the backend half of T3 (live `/ws` feed) first: T1, T2, T6 and T11 all depend on it.

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
- **Demo impact**: Highest.
- **[Audit]** Not pure frontend: the `/ws` stream must first be rewired to live data (see T3). Band limits must come from the `thresholds` object in the WebSocket payload, not constants — the live thresholds are 75/95 (degradation), 55/85 (unstable), 80 (recovery) and 45 (quarantine, set in compose), not the 30/60/80/90 shown below.

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
- **Person B (Sam)**: Deliver `state`, `sub_scores` and `thresholds` in the live WebSocket payload (done as part of T3).
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
| 💀 Full Outage | `{mode:"timeout", rate:1.0, latency_ms:6000}` | ERI collapses, QUARANTINED, HTTP halts |
| 🔄 Recovery | `{mode:"success", rate:0}` + admin restore | RECOVERING probe, ERI climbs above the recovery threshold |
| ⚡ Rate Limiting | `{mode:"rate_limit", rate:0.8}` | 429s → success_rate radar spike |
| 🔑 Auth Failure | `{mode:"auth_failure", rate:1.0}` | HTTP 401, different ERI sub-metric pattern |
| 📡 Network Partition | `{mode:"connection_reset", rate:1.0}` | Transport errors with no HTTP status. **[Audit]** These are not timeouts, so the timeout sub-metric will not dominate; set the label from what is observed |

**[Audit] corrections**
- Presets are defined on the server; the frontend sends only a scenario id (`normal`, `early_degradation`, `instability`, `full_outage`, `recovery`, `rate_limiting`, `auth_failure`, `network_partition`).
- `timeout` mode only sleeps `latency_ms` and then returns 200, so the preset needs `latency_ms` above the worker timeout (5 s by default; the preset uses 6000). Set `RELAY_WEBHOOK_TIMEOUT_SECONDS=2` on the dispatcher so the outage collapses quickly.
- The API reaches the simulator over HTTP (`SIMULATOR_URL`). The demo endpoint and subscription must be created idempotently, or every restart doubles deliveries.
- Auto-published events go through the same persist-then-publish path as `POST /events`.
- The demo router is mounted only when `RELAY_DEMO_ENABLED=true`.
- Additional endpoints: `GET /demo/scenarios`, `GET /demo/status`, `POST /demo/stop`.

**[Built]** Observed on the compose stack:
- Full Outage goes from `OBSERVED_DEGRADATION` straight to `QUARANTINED` in about 6 s and does not pass through `UNSTABLE` (the compose quarantine threshold is 45).
- Recovery reaches `AVAILABLE` about 3 s after restore: the first good probe ends `RECOVERING`. A longer, visible probation needs a minimum sample count in the state machine, which is not built.
- `DEMO_TARGET_URL` is optional and defaults to `SIMULATOR_URL` + `/webhook`; compose does not set it.
- Person B made the compose changes for this task.

**[Built] Bug caught in review: duplicate demo endpoint**
- What went wrong: clicking a scenario button makes the API check whether the demo endpoint exists and create it if it does not. Two clicks arriving at the same instant could both get the answer "no" and both create one. With two demo endpoints, every demo event is delivered twice.
- Fix: the demo endpoint and its subscription now get fixed ids worked out from the owner, the URL and the event type. Saving a row with an id that already exists updates that row instead of adding one. Two requests that both decide to create therefore write the same rows, and only one endpoint and one subscription exist. This holds for any number of API containers and needs no schema change.
- A first version of the fix used a lock inside the API process. It was replaced because a lock in one container cannot stop a second container.

### Division of Work
- **Person B (Sam)**:
  - Create `relay/api/demo.py` with `POST /demo/scenario` (configures simulator, launches background event publishing task).
  - Include demo router in `relay/api/main.py`.
  - Add the 4 missing simulator modes in `simulator/config.py` and `simulator/service.py` (`success`, `mixed`, `auth_failure` 401, `connection_reset`).
  - Fix the restore path so the Recovery button works (see T11).
- **Person C (Suhan)**:
  - Add scenario button grid to `relay/dashboard/frontend/src/App.jsx`.
  - Wire button click handlers to `fetch('/demo/scenario')`.
  - Add active scenario badge banner.

### Files
- `relay/api/demo.py` — **NEW** (Person B)
- `relay/api/main.py` — include demo router (Person B)
- `simulator/config.py`, `simulator/service.py`, `simulator/main.py` — add missing failure modes (Person B)
- `relay/api/events.py` — extract shared ingest helper (Person B, Person A review)
- `relay/infrastructure/persistence/endpoint_repository.py` — lookup by owner + target URL (Person B, Person A review)
- `docker-compose.yml` — `SIMULATOR_URL`, `DEMO_TARGET_URL`, `RELAY_DEMO_ENABLED`, `RELAY_WEBHOOK_TIMEOUT_SECONDS` (Person A)
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
  - **[Audit]** Rewire `/ws` from the synthetic `DashboardDemoSource` to the live `state_store` (fed from Kafka), keyed by `?endpoint_id=`. This is a prerequisite for T1, T2, T6 and T11.
  - Add `eri_sub_scores`, `eri_weights`, `thresholds`, `endpoint_id` and `source` (`live` / `synthetic`) to the payload; add `state` and `sub_scores` to each `eri_history` point.
  - Fill `delivery_events` from Postgres (read-only query of recent attempts).
  - Keep the synthetic source only behind `DASHBOARD_SYNTHETIC_SOURCE=true`; no silent fallback.
  - Add CORS middleware to the dashboard, API and simulator apps.
- **Person C (Suhan)**:
  - Add Recharts `RadarChart`, `PolarGrid`, `PolarAngleAxis`, and `Radar` components in `App.jsx`.
  - Bind radar axes to live WebSocket `eri_sub_scores` values.

### Files
- `relay/dashboard/websocket.py`, `adapter.py`, `models.py`, `main.py` — live WS feed (Person B)
- `relay/infrastructure/persistence/delivery_attempt_repository.py` — `list_recent_by_endpoint()` (Person B)
- `relay/dashboard/frontend/src/App.jsx` — `RadarChart` UI component; WebSocket URL to port 8090 (Person C)

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
   **[Audit]** The numbers above are placeholders. Measured on scenario 10 (seed 42): success rate is
   0.6423 for A, B and C; wasted retries are 930 / 626 / 428; recovery time is 0 / 0 / 187 events
   (A and B have no state machine). Cards must render the `comparison` object computed by the API.
   Headline metric: wasted retries. No MTTR card unless recovery time is defined for A and B.

   **[Built]** Over the 8 held-out scenarios (seed 42) C cuts wasted retries by 79.7% against A
   and 67.0% against B, with the same mean success rate (0.5912). Do not present total retry
   delay as a win: C's is far higher (190,646 s against 4,435 s for A and 13,340 s for B)
   because it deliberately waits and quarantines. The API returns all three.

### Division of Work
- **Person B (Sam)**:
  - Add `POST /api/chart/benchmark/run` in `relay/api/chart/router.py`. All 8 held-out scenarios × 3 strategies run in under a second, so no background job is needed.
  - **[Audit]** Bridge the two stores: `BenchmarkRunner` writes to `benchmark.metrics.benchmark_store`, the router reads `relay.api.chart.stores.benchmark_store`.
  - Extend the run schema with success rate, wasted retries, recovery time, quarantine count and total delay.
  - Format scenario metric comparison JSON payloads.
- **Person C (Suhan)**:
  - Implement Benchmark Tab, strategy run buttons, grouped bar chart, and KPI stat cards in `App.jsx`.

### Files
- `relay/api/chart/router.py`, `schemas.py`, `stores.py` — `POST /benchmark/run`, extended run schema, store bridge (Person B)
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

**[Audit] corrections**
- The DLQ is defined as deliveries that are `FAILED`, `DEAD_LETTER`, or parked (`PENDING` with no attempt, skipped under PAUSE/QUARANTINE). A `DEAD_LETTER`-only query stays empty during an outage.
- Republishing a dead event to `events.business` is a no-op: the delivery is unique on `(event_id, subscription_id)`, terminal deliveries return early, and attempt 1 is already claimed.
- Replay design: the API-side `ReplayManager` publishes one request per delivery to a new topic `events.internal.replay`. The dispatcher handles it through the normal dispatch path, so AEDE still supplies the policy. A parked delivery is re-dispatched as is. A failed delivery gets a new Event (same type, payload and correlation id; causation id = original event id) dispatched to that one subscription, giving a new Delivery with attempt #1. Original rows are never modified.
- Operator replay is gated by isolation action only (no HTTP under PAUSE/QUARANTINE). `replay_policy.allowed` is false in `AVAILABLE`, which is when the demo script replays.

**[Built]** As designed above; recorded in ADR 0021 (Proposed). A parked delivery counts as dead-lettered once it has been `PENDING` with no attempt for more than 5 s. A repeat replay of the same delivery is refused. A database carried over from earlier sessions holds thousands of old parked deliveries on other endpoints, so the DLQ panel should always query with `endpoint_id`.

### Division of Work
- **Person B (Sam)**:
  - Create `relay/data_plane/replay/manager.py` implementing `ReplayManager.replay_dead_letters()`: selects DLQ deliveries and publishes replay requests to `events.internal.replay`.
  - Add the topic to `PLATFORM_TOPICS` and a replay handler in `relay/data_plane/dispatcher/main.py` (offset committed only after the work is persisted).
  - Add `POST /replay` in `relay/api/endpoints.py`.
  - Add `GET /api/chart/dlq` in `relay/api/chart/router.py`.
  - Add `get_dead_letters()` in `relay/infrastructure/persistence/delivery_repository.py` (status lives on deliveries, not attempts); excludes deliveries already replayed.
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
- `relay/infrastructure/persistence/delivery_repository.py` — add `get_dead_letters()` (Person B, Person A review)
- `relay/infrastructure/messaging/admin.py` — add `events.internal.replay` topic (Person B, Person A review)
- `relay/data_plane/dispatcher/main.py` — replay handler (Person B)
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

**[Audit] corrections**
- `prometheus_client` is not yet a dependency in `pyproject.toml`.
- Worker and AEDE metrics are emitted in the dispatcher process, which has no HTTP server. The dispatcher needs its own metrics port and a Prometheus scrape target; the API's `/metrics` will not show them.
- `ENDPOINT_STATE` as a Gauge labelled `[endpoint_id, from_state, to_state]` cannot express transitions. Use a transition Counter plus a current-state Gauge.

### Division of Work
- **Person A**:
  - Implement `relay/infrastructure/monitoring/metrics.py`.
  - Add Prometheus `Instrumentator` to `relay/api/main.py`.
  - Create `deploy/prometheus.yml` and `deploy/grafana/provisioning/datasources/prometheus.yml`.
  - Update `docker-compose.yml` with prometheus and grafana services.
- **Person B (Sam)**:
  - Wire metric emissions into `relay/data_plane/workers/webhook.py` (attempts, retry histograms).
  - Wire metric emissions into `relay/control_plane/aede/service.py` (ERI gauge, state transitions).
  - Start the metrics exporter in `relay/data_plane/dispatcher/main.py`.
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
- **Person B (Sam)**: Add `GET /api/chart/benchmark/multi-endpoint` returning the results of `benchmark/multi_endpoint.py`. Measured for the healthy neighbor: throughput 1.39 / 1.98 / 16.58 events/s and mean queue wait 52.7 s / 39.3 s / 4.0 s for A / B / C.
- **Person C (Suhan)**: Build side-by-side throughput bars and isolation badges in `App.jsx`.

### Files
- `relay/api/chart/router.py`, `schemas.py` — multi-endpoint endpoint (Person B)
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

**[Audit] corrections**
- The synthesizer must not read the environment: that is a hidden input to a function that must be deterministic. `hmac_required` is read once at process start and passed to `synthesize_policy` as an explicit parameter. Strategies A and B and the Tier 3 fallback take the same parameter so signing survives fallback.
- Attempts do not store request headers, so the badge has no data source in Postgres. The simulator keeps a ring buffer of received requests with the verification result at `GET /simulator/deliveries`; the frontend joins on `attempt_id`.
- The simulator container also needs `RELAY_WEBHOOK_SIGNING_SECRET`.

**[Built]** As corrected above. The simulator answers 401 to a bad signature and records each request as `verified`, `invalid`, `unsigned` or `unverifiable`. Person B made the compose changes.

### Division of Work
- **Person B (Sam)**:
  - Create `relay/infrastructure/auth/signing.py` and `relay/infrastructure/auth/__init__.py`.
  - Refactor `relay/data_plane/workers/webhook.py` to import from `auth.signing`.
  - Add an `hmac_required` parameter to `synthesize_policy`, set from config in `AEDEService` and the strategies.
  - Update `simulator/main.py` to verify signatures and log verification.
- **Person A**: Add signing secret env vars to `docker-compose.yml`.
- **Person C (Suhan)**: Display signature badge in `App.jsx` timeline rows.

### Files
- `relay/infrastructure/auth/signing.py` — **NEW** (Person B)
- `relay/infrastructure/auth/__init__.py` — export signing helpers (Person B)
- `relay/data_plane/workers/webhook.py` — refactor import (Person B)
- `relay/control_plane/aede/synthesizer.py`, `service.py`, `strategies/*.py` — `hmac_required` parameter (Person B)
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
  - **[Audit]** Today it does not. Restore only flips in-memory state: no policy event is published, and the cached QUARANTINE policy keeps being served and re-cached, so delivery is unlikely to resume. Suspected from code reading; the only passing demo run used mocks. Reproduce on the compose stack, then fix in `relay/control_plane/aede/service.py` and `relay/data_plane/dispatcher/main.py`: replace the cached policy, save an explainability record, publish a policy evaluation event.
  - **[Built]** Reproduced: the endpoint re-quarantined within a second of restore and stayed there. Fixed in `AEDEService.restore_and_publish` (ADR 0020, Proposed), which also resets the ERI window and ignores attempts that started before the restore. The live restore now differs from the benchmark's restore model, which keeps the window; the benchmark path is unchanged.

### Files
- `relay/dashboard/frontend/src/App.jsx` — alert toast & banner components (Person C)
- `relay/dashboard/frontend/src/App.css` — toast styles (Person C)

---

## T12 — Delivery Adapter Interface Formalization (Tier 1 Architecture Gap)
- **Primary Owner**: **Person B (Sam)** (Protocol formalization & worker refactor)
- **Support**: **Person A** (Dispatcher injection review)
- **Closes**: `relay/adapters/webhook/` empty stub. Architecture correctness.

### What to build
1. Formalize `DeliveryAdapter` protocol in `relay/kernel/interfaces/delivery_adapter.py` (**[Audit]** new file; it does not exist yet). No `httpx` types in the kernel.
2. Extract HTTP dispatch from `WebhookWorker._post()` into `relay/adapters/webhook/adapter.py`.
3. Inject `DeliveryAdapter` into `WebhookWorker` constructor.

**[Built]** Done. The worker still accepts `client=` and `timeout_seconds=` for older callers and passes them to the default webhook adapter.

### Division of Work
- **Person B (Sam)**:
  - Add `DeliveryAdapter` Protocol to `relay/kernel/interfaces/delivery_adapter.py`.
  - Create `WebhookDeliveryAdapter` in `relay/adapters/webhook/adapter.py`.
  - Refactor `relay/data_plane/workers/webhook.py` to use injected adapter.
  - Update `relay/data_plane/dispatcher/main.py` to construct and inject the adapter.
- **Person A**: Review interface separation.

### Files
- `relay/kernel/interfaces/delivery_adapter.py` — **NEW** Protocol (Person B)
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

## T14 — Repeat Endpoint Registration (Optional, Found in Review)
- **Primary Owner**: **Person A** (Decision, and the change if one is made)
- **Support**: **Person B (Sam)** (Review)
- **Status**: optional. It is not a bug fix and the demo does not depend on it.

### The question
`POST /endpoints` lets one owner register the same URL twice. Each registration gets its own
endpoint id, so AEDE tracks two separate health scores for what is one real endpoint. The review
that found the duplicate demo endpoint bug (see T2) raised this. The demo bug itself is fixed
without touching it.

### Options
1. **Leave it.** An owner who registers a URL twice gets two endpoints. No work.
2. **Reject or reuse on repeat.** Return the existing endpoint, or 409. Doing this safely needs a
   unique rule on `endpoints (owner_id, target_url)`, and existing databases already hold
   duplicates, so the migration must clean those up first without rewriting delivery history.

Person A picks one and records the reason. Option 2 is a schema and API behaviour change, so it
should not be started before the MVP checklist is complete.

### Files (option 2 only)
- `relay/infrastructure/persistence/migrations/versions/<new>.py` — **NEW** (Person A)
- `relay/infrastructure/persistence/models.py`, `endpoint_repository.py` (Person A)
- `relay/api/endpoints.py` — repeat-registration behaviour (Person A)

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
| `relay/infrastructure/persistence/delivery_attempt_repository.py` | Add `list_recent_by_endpoint()` for the timeline | T3 | **Person B (Sam)** |
| `relay/dashboard/websocket.py`, `adapter.py`, `models.py`, `main.py` | Rewire `/ws` to live `state_store`; add sub-scores, thresholds, delivery events; CORS | T3 | **Person B (Sam)** |
| `relay/infrastructure/persistence/delivery_repository.py` | Add `get_dead_letters()` | T5 | **Person B (Sam)** |
| `relay/infrastructure/messaging/admin.py` | Add `events.internal.replay` topic | T5 | **Person B (Sam)** |
| `simulator/config.py`, `simulator/service.py` | Add `success`, `mixed`, `auth_failure`, `connection_reset` modes | T2 | **Person B (Sam)** |
| `relay/api/chart/schemas.py`, `stores.py` | Extended benchmark run schema, store bridge, multi-endpoint schema | T4, T8 | **Person B (Sam)** |
| `relay/infrastructure/monitoring/metrics.py` | **NEW** — Prometheus metrics | T7 | **Person A** |
| `relay/data_plane/workers/webhook.py` | Emit metrics; import auth.signing; inject adapter | T7, T9, T12 | **Person B (Sam)** |
| `relay/control_plane/aede/service.py` | Emit ERI + state metrics; fix restore path; pass `hmac_required` | T7, T9, T11 | **Person B (Sam)** |
| `relay/infrastructure/auth/signing.py` | **NEW** — signing helpers | T9 | **Person B (Sam)** |
| `relay/infrastructure/auth/__init__.py` | Export signing helpers | T9 | **Person B (Sam)** |
| `relay/control_plane/aede/synthesizer.py` | Accept `hmac_required` as an explicit parameter | T9 | **Person B (Sam)** |
| `docker-compose.yml` | Add prometheus, grafana, signing env vars, demo env vars, worker timeout, dispatcher metrics port, CORS origins | T2, T7, T9 | **Person A** |
| `deploy/prometheus.yml` | **NEW** | T7 | **Person A** |
| `deploy/grafana/provisioning/datasources/prometheus.yml` | **NEW** | T7 | **Person A** |
| `deploy/grafana/dashboards/relay.json` | **NEW** | T7 | **Person B (Sam)** |
| `deploy/k8s/*.yaml` | **NEW** (~15 files) | T10 | **Person A** |
| `deploy/README-k8s.md` | **NEW** | T10 | **Person A** |
| `simulator/main.py` | Config read endpoint; signature verification; received-request ring buffer | T2, T9 | **Person B (Sam)** |
| `relay/kernel/interfaces/delivery_adapter.py` | **NEW** — `DeliveryAdapter` Protocol | T12 | **Person B (Sam)** |
| `relay/adapters/webhook/adapter.py` | **NEW** (moved HTTP from webhook.py) | T12 | **Person B (Sam)** |
| `relay/data_plane/dispatcher/main.py` | Inject WebhookDeliveryAdapter; replay handler; restore fix; metrics exporter | T5, T7, T11, T12 | **Person B (Sam)** |
| `relay/kernel/events/*.py` | **NEW** — 6 typed event model files | T13 | **Person A** & **Person B (Sam)** |
| `relay/api/endpoints.py`, `relay/infrastructure/persistence/` | Optional: repeat endpoint registration behaviour | T14 | **Person A** |

---

## 📅 Revised Sequencing (Multi-Track Sprint Schedule)

**[Built]** Person B's column is complete except the T7 metrics wiring (Day 5) and the T13 event
models (Day 6), which wait on Person A. Person A's and Person C's columns have not started.

```
        PERSON B (Sam)                PERSON C (Suhan)              PERSON A
        [Cross-Domain Engine]         [Dashboard Frontend]          [Infra & Core]
───────────────────────────────────────────────────────────────────────────────────────
Day 1:  • Publish WS + REST contracts  • T1: ComposedChart with     • T13: Draft typed event
        • T3: Rewire /ws to live        bands & hysteresis lines      base models in kernel
          state_store
        • Reproduce restore bug (T11)
        • T2: Simulator failure modes

Day 2:  • T2: Build relay/api/demo.py  • T2: Build Scenario Preset  • T7: Implement
          scenario engine               buttons in App.jsx            monitoring/metrics.py
        • T11: Fix restore path        • T3: Implement Recharts       & Prometheus scrape
        • T5: Start ReplayManager       RadarChart                    config

Day 3:  • T5: Finish replay topic,     • T5: Build DLQ table &      • T7: Configure
          POST /replay, GET /dlq        replay action buttons         Prometheus & Grafana
        • T4: Add /benchmark/run       • T4: Build Benchmark tab      in docker-compose.yml
          & bridge benchmark stores     grouped bar chart UI

Day 4:  • T9: Extract auth/signing.py  • T6: Implement State        • T10: Begin Kubernetes
          & wire HMAC policy flag       Transition Journal            manifests (namespace,
        • T12: Formalize               • T11: Build Quarantine        configmap, secrets,
          DeliveryAdapter interface     toast alerts & banner         statefulsets)

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
| 0:00 | `docker compose up` → open browser → services panel | **Person A** | All services green: Kafka, PG, Redis, API, Dispatcher, Simulator, Dashboard today; Prometheus and Grafana once T7 lands |
| 1:00 | Click **▶ Normal Operation** | **Person C** | ERI=100, AVAILABLE badge, Timeline shows green ✅ rows, Radar full, Signature header visible |
| 2:00 | Click **⚠ Early Degradation** | **Person C** | ERI line enters yellow band → OBSERVED_DEGRADATION, Policy: "reduced concurrency" |
| 3:30 | Click **🔴 Instability** | **Person C** | ERI enters orange band → UNSTABLE, backoff delay visible in policy panel, alert toast |
| 5:00 | Click **💀 Full Outage** | **Person C** | ERI collapses → 🚨 QUARANTINE ALERT toast → Timeline shows no new HTTP rows → DLQ panel fills |
| 6:30 | Click **🔄 Recovery** | **Person C** | RECOVERING probe → ERI back above the recovery threshold (80) → AVAILABLE. This takes about 3 s today |
| 7:00 | Click **[Replay All]** in DLQ panel | **Person B** | Events requeue, Timeline shows replayed deliveries succeeding |
| 7:30 | Switch to Grafana tab | **Person A** | Real-time ERI gauge, retry histogram, state transition markers |
| 8:00 | Switch to Benchmark tab | **Person B** | Click "Run Scenario 10 — all strategies" → grouped bar chart renders: C cuts wasted retries (428 vs 930 for A and 626 for B); success rate is equal across strategies |
| 9:00 | Switch to Multi-Endpoint tab | **Person B** | Show throughput bars: the healthy neighbor is starved under Strategy A (1.39 events/s) and protected under Strategy C (16.58 events/s) |
| 9:30 | Show State Journal panel | **Person C** | Full journey scrollable: AVAILABLE → DEGRADED → UNSTABLE → QUARANTINED → RECOVERING → AVAILABLE |

**[Built]** Notes for rehearsal:
- UNSTABLE appears at 3:30 from the Instability preset. Full Outage on its own skips it.
- The 7:30 Grafana step depends on T7, which is not started.
- No frontend work for these steps exists yet; every step from 1:00 on depends on Person C's UI.

---

## What Is Still Out of Scope (Unchanged from Original)

- Terraform HCL provisioning
- CLI tool  
- Multi-language SDKs (Java, Node, Go)
- External email/Slack notification service (dashboard toast is the agreed alternative)
- Multi-tenant RBAC
- OpenTelemetry distributed tracing
- Additional delivery adapters (Kafka, RabbitMQ, etc.)
