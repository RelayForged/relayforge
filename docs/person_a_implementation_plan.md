# Person A — Implementation Plan

Scope: everything assigned to Person A in `docs/demo_implementation_plan.md`.
You own **T7** (metrics module, Prometheus, Grafana provisioning, Compose services), **T10** (Kubernetes manifests), and **T13**'s base (`base.py`, `delivery_events.py`, `operational_events.py`). **T14** is an optional decision, recorded after T7 and T10, not built before them.
You support **T9** and **T12** by review only. Both are already built. You do not rebuild them.

Written against `dev` at `8c24ed5` (`docs(plan): reduce T14 to an optional question for Person A`). Person B's backend (T2, T3, T4, T5, T8, T9, T11, T12) is on this branch. Person B's remaining items — metric emissions, the Grafana panel JSON, and `policy_events.py` / `endpoint_events.py` / `replay_events.py` — start only after the contracts in section 3 land.

Branch: `feature/person-a-observability`, cut from this `dev`. Do not continue `feature/person-a-kafka-backbone`. That sprint is already on `dev`.

---

## 0. Status (2026-10-13, branch `dev` @ `8c24ed5`)

| Item | State |
|---|---|
| T7 metrics module, Prometheus, Grafana | Not started when this plan was written; implemented on this branch. `relay/infrastructure/monitoring/` was an empty stub package (only `.gitkeep`). `prometheus_client` is not in `pyproject.toml`. Compose has no Prometheus or Grafana service. The dispatcher starts no metrics port. |
| T10 Kubernetes manifests | Not started when this plan was written; implemented on this branch. `deploy/` did not exist. |
| T13 typed events, Person A's files | Not started when this plan was written; implemented on this branch. `relay/kernel/events/` was an empty stub package (only `.gitkeep`). |
| T14 repeat endpoint registration | Open. Optional. `POST /endpoints` still inserts a new row per call. No unique key on `(owner_id, target_url)`. |
| T9 Compose signing env | Done by Person B. Dispatcher has `RELAY_HMAC_REQUIRED=true`, `RELAY_WEBHOOK_SIGNING_SECRET`, and `RELAY_WEBHOOK_TIMEOUT_SECONDS=2`. Simulator has the same secret. Do not redo this. |
| T2 Compose demo env | Done by Person B. API has `RELAY_DEMO_ENABLED=true` and `SIMULATOR_URL=http://simulator:8080`. `DEMO_TARGET_URL` is optional and unset; the demo code defaults it. |
| CORS | Done in code. `relay/infrastructure/cors.py` defaults to the Vite origins (`http://localhost:5173`, `http://127.0.0.1:5173`). API and dashboard already mount the middleware. Do not add a frontend container (D4). |
| T5 / T12 review | Done on this branch (replay topic, DLQ query, delivery adapter). No further review unless those files change. |

---

## 1. Where the source plan differs from this tree

Read these before writing code. They change the shape of T7.

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| F1 | Worker and AEDE metrics run inside the dispatcher process. The dispatcher is `python -m relay.data_plane.dispatcher.main`. It has no HTTP server. | `docker-compose.yml` dispatcher `command`; no `start_http_server` anywhere under `relay/` | `GET /metrics` on the API will not show attempt, ERI, or state series. Prometheus needs a second scrape target. See D1 and D2. |
| F2 | The source plan's `ENDPOINT_STATE` Gauge labelled `[endpoint_id, from_state, to_state]` cannot represent a transition. A gauge is a current value; those labels describe a change. | `docs/demo_implementation_plan.md` T7 audit note | Ship a transition Counter plus a current-state Gauge. See D1. Person B emits both. You own the names. |
| F3 | `prometheus_client` and `prometheus-fastapi-instrumentator` are not dependencies. The image installs `pyproject.toml` dependencies only (`Dockerfile` runs `pip install -e ".[dev]"`). | `pyproject.toml` | Both libraries go in the main `dependencies` list, not an extra. An extra would be missing from the Compose image. |
| F4 | Compose already has the T2 and T9 environment Person B asked you for. | `docker-compose.yml` `api`, `dispatcher`, `simulator` | The only Compose edits left for you are Prometheus, Grafana, and `RELAY_METRICS_PORT` on the dispatcher. |
| F5 | Nothing in Compose serves the React app. The frontend WebSocket URL still points at port 8000. | `docs/demo_implementation_plan.md` status table; `relay/dashboard/frontend/src/App.jsx` | The port fix is Person C's. Serving the built UI from Compose is a decision, not a task in this plan. See D4. |
| F6 | `relay/kernel/` is frozen for existing modules (ADR 0010). T13 is new files inside the existing empty `relay/kernel/events/` stub package. | `relay/kernel/entities/event.py` and the rest of `relay/kernel/` | Add `relay/kernel/events/` only. Do not edit entities, policies, value objects, or interfaces that already exist. |
| F7 | Grafana panel JSON is Person B's file (`deploy/grafana/dashboards/relay.json`). | T7 division of work | You provision the datasource and a file provider. You do not draw panels. |
| F8 | `POST /endpoints` creates a new endpoint id on every call, including a repeated URL for the same owner. | `relay/api/endpoints.py` `create_endpoint`; `endpoints` has an index on `owner_id` and no unique constraint | T14. The demo does not depend on it. See D5. |

---

## 2. Decisions

The work items below assume the recommendation in each case.

**D1. Metric names and labels (T7).** Recommended set, all in one registry in `relay/infrastructure/monitoring/metrics.py`:

| Name | Type | Labels | Meaning |
|---|---|---|---|
| `relay_delivery_attempts_total` | Counter | `endpoint_id`, `status` | One increment per completed attempt. `status` is `success`, `http_error`, `timeout`, or `transport_error`. |
| `relay_eri_score` | Gauge | `endpoint_id` | Latest ERI, 0–100. |
| `relay_endpoint_state` | Gauge | `endpoint_id`, `state` | Current state. Person B sets `1` on the active state and `0` on the previous one. |
| `relay_endpoint_state_transitions_total` | Counter | `endpoint_id`, `from_state`, `to_state` | One increment per transition. |
| `relay_delivery_retries` | Histogram | none | Attempt number observed when a delivery reaches a terminal status. Buckets `1, 2, 4, 8, 16`. |

`endpoint_id` cardinality is acceptable for the demo. Do not add `from_state` / `to_state` to the state Gauge.

**D2. Two scrape targets (T7).** Recommended:
- API process: `prometheus-fastapi-instrumentator` exposes `GET /metrics` on port 8000. That series is HTTP traffic only.
- Dispatcher process: `start_metrics_server(port)` in `metrics.py` calls `prometheus_client.start_http_server`. Default port `9100`, override `RELAY_METRICS_PORT`. Person B calls this once from `dispatcher/main.py` and then emits D1. You do not edit the worker or AEDE to emit.

Prometheus scrapes `api:8000` and `dispatcher:9100`. Publish `127.0.0.1:9100:9100` on the dispatcher so the host can curl it. Until Person B calls `start_metrics_server`, the dispatcher target is down. That is expected. Do not start the server yourself inside the worker.

**D3. Grafana dashboard file (T7).** You add the datasource and a file provider that loads `/etc/grafana/dashboards`. Compose mounts `deploy/grafana/dashboards` there. Person B adds `relay.json`. You commit `deploy/grafana/dashboards/.gitkeep` so the directory exists. You do not author panels.

**D4. React app in Compose (F5).** Recommended: do not add a frontend service. Person C runs Vite on the host at port 5173. CORS already allows that origin. Record this in `docs/small-decisions-personA/react-dev-server.md`. The WebSocket URL change (8090, not 8000) stays with Person C.

**D5. Repeat endpoint registration (T14).** Recommended: leave it (option 1). An owner who registers the same URL twice gets two endpoints and two ERI scores. Record that in `docs/small-decisions-personA/repeat-endpoint-registration.md` after T7 and T10. Option 2 (reject or reuse, unique on `(owner_id, target_url)`, cleanup migration) is a schema and API change. Do not start it in this plan. Existing databases already contain duplicate URLs, and historical delivery rows must not be altered.

**D6. Dispatcher autoscaling (T10).** Recommended: CPU `HorizontalPodAutoscaler` on `relay-api` and on `relay-dispatcher`. Also a KEDA `ScaledObject` for the dispatcher on Kafka consumer lag, group `relay-dispatcher`. The dispatcher group reads four topics (`events.business`, `events.internal.delivery`, `events.internal.endpoint`, `events.internal.replay`), so the trigger sets no `topic` and measures lag across all of them. The CPU HPA and the ScaledObject are mutually exclusive: apply one, not both. The CPU HPA works without KEDA installed. The ScaledObject is what the MVP checklist means by "HPA scaling on Kafka lag". Person B reviews the lag trigger and the probe paths. You do not install a cluster.

**D7. Secrets in manifests (T10).** `deploy/k8s/secret.yaml` holds placeholders (`CHANGE_ME`), not the Compose dev passwords. The README says to replace them before apply. Compose dev credentials stay in `docker-compose.yml` only.

---

## 3. Contract you publish (Day 1 for events, Day 2 for metrics)

Person B builds against this. Send it when the files exist, not before.

### 3.1 Metrics (`relay/infrastructure/monitoring/metrics.py`)

```python
def start_metrics_server(port: int | None = None) -> int:
    """Bind prometheus_client's HTTP server. Default port from RELAY_METRICS_PORT or 9100.
    Idempotent within a process. Returns the bound port.
    """

# Module-level collectors matching D1. Import and use these. Do not construct a second registry.
# DELIVERY_ATTEMPTS, ERI_SCORE, ENDPOINT_STATE, STATE_TRANSITIONS, RETRY_COUNT
```

Person B:
- Calls `start_metrics_server()` once at dispatcher startup, before consuming.
- Increments `DELIVERY_ATTEMPTS` from the worker path after the attempt is persisted.
- Sets `ERI_SCORE` and `ENDPOINT_STATE`, and increments `STATE_TRANSITIONS`, from `AEDEService` after an evaluation.
- Observes `RETRY_COUNT` when a delivery becomes terminal.
- Writes `deploy/grafana/dashboards/relay.json` with an ERI gauge, attempts by status, the retry histogram, and transition markers. The provider in section 4.2 loads that file with no further Compose change.

### 3.2 Internal events (`relay/kernel/events/base.py`)

Frozen Pydantic v2, same style as `relay/kernel/entities/event.py`. `correlation_id` and `causation_id` use `CorrelationID` and `CausationID`. `occurred_at` is `datetime` with a UTC `default_factory`. Subclasses add fields only. No methods that read the clock or the environment.

Person A ships these subclasses. Person B's modules import `InternalEvent` from `base.py` and do not redefine it.

| Class | Module | Fields beyond the base |
|---|---|---|
| `DeliveryAttemptStarted` | `delivery_events.py` | `delivery_id`, `attempt_id`, `attempt_number`, `endpoint_id`, `subscription_id` |
| `DeliveryAttemptCompleted` | `delivery_events.py` | `delivery_id`, `attempt_id`, `endpoint_id`, `http_status: int \| None`, `success: bool`, `latency_ms: float`, `timed_out: bool`, `error_message: str \| None` |
| `DeliveryFailed` | `delivery_events.py` | `delivery_id`, `endpoint_id`, `business_event_id`, `attempt_count`, `status` (`FAILED` or `DEAD_LETTER`) |
| `DeadLetterRouted` | `operational_events.py` | `delivery_id`, `endpoint_id`, `business_event_id`, `reason` (`FAILED`, `DEAD_LETTER`, or `PARKED`) |
| `AlertTriggered` | `operational_events.py` | `endpoint_id`, `state`, `eri: float`, `reason` |

Switching live Kafka payloads over to these types is out of scope. They are the domain model only.

---

## 4. Work items, in build order

### 4.1 Typed events, Person A's half (T13)

Files: `relay/kernel/events/__init__.py`, `base.py`, `delivery_events.py`, `operational_events.py`. Tests in `tests/unit/test_internal_events.py`.

1. `InternalEvent` as in section 3.2. `model_config = ConfigDict(frozen=True)`.
2. The five subclasses in the table. Export them from `__init__.py`.
3. Do not import `httpx`, `aiokafka`, FastAPI, or SQLAlchemy. This package stays on the kernel dependency set (`pydantic` and the existing identifier types).
4. Do not edit `relay/kernel/entities/`, `policies/`, `value_objects/`, `interfaces/`, or `contracts/`.
5. Tests: assignment to a field raises; `model_validate(model_dump())` round-trips; `model_dump_json()` parses back with `model_validate_json`.

Small decision: `docs/small-decisions-personA/internal-event-models.md` — Context, Decision, Why. One paragraph on why these types are not yet the Kafka payload.

### 4.2 Prometheus metrics and Grafana provisioning (T7)

Files: `relay/infrastructure/monitoring/__init__.py`, `metrics.py`, `relay/api/main.py`, `pyproject.toml`, `deploy/prometheus.yml`, `deploy/grafana/provisioning/datasources/prometheus.yml`, `deploy/grafana/provisioning/dashboards/dashboards.yml`, `deploy/grafana/dashboards/.gitkeep`, `docker-compose.yml`. Tests in `tests/unit/test_metrics.py`.

1. Add `prometheus-client` and `prometheus-fastapi-instrumentator` to `[project].dependencies`.
2. Implement the collectors and `start_metrics_server` from section 3.1. One `CollectorRegistry` owned by this module, set as the default registry the server exports, so the API instrumentator and this module do not split series across registries inside the API process. The dispatcher process is separate; it imports the same module and gets its own registry in its own memory.
3. In `relay/api/main.py`, attach the instrumentator and expose `/metrics`. Do not add business metrics there.
4. `deploy/prometheus.yml`: scrape `api:8000` path `/metrics` and `dispatcher:9100` path `/metrics`. Image `prom/prometheus:v2.51.0`.
5. Grafana datasource provisioning points at `http://prometheus:9090`. Dashboard provider loads the mounted dashboards directory. Image `grafana/grafana:10.4.0`.
6. Compose services `prometheus` and `grafana`. Bind host ports to `127.0.0.1` (`9090` and `3000`), same as the other services. Grafana admin password from `GF_SECURITY_ADMIN_PASSWORD`, default suitable for local only. Add `RELAY_METRICS_PORT: "9100"` and `127.0.0.1:9100:9100` on the dispatcher.
7. Tests: collector names and label sets match D1; `start_metrics_server` reads `RELAY_METRICS_PORT`; a second call in-process does not raise. No live Prometheus in unit tests.

Small decision: `docs/small-decisions-personA/prometheus-scrape-targets.md`.

Tell Person B when `metrics.py` is importable. That unblocks section 4.8 of `docs/sam_implementation_plan.md`.

### 4.3 Kubernetes manifests (T10)

Files, all new:

```
deploy/k8s/namespace.yaml
deploy/k8s/configmap.yaml
deploy/k8s/secret.yaml
deploy/k8s/kafka/statefulset.yaml
deploy/k8s/postgres/statefulset.yaml
deploy/k8s/redis/deployment.yaml
deploy/k8s/relay-api/deployment.yaml
deploy/k8s/relay-api/service.yaml
deploy/k8s/relay-api/ingress.yaml
deploy/k8s/relay-api/hpa.yaml
deploy/k8s/relay-dispatcher/deployment.yaml
deploy/k8s/relay-dispatcher/hpa.yaml
deploy/k8s/relay-dispatcher/scaledobject.yaml
deploy/k8s/relay-dashboard/deployment.yaml
deploy/k8s/relay-dashboard/service.yaml
deploy/k8s/relay-simulator/deployment.yaml
deploy/k8s/relay-simulator/service.yaml
deploy/k8s/prometheus/deployment.yaml
deploy/k8s/grafana/deployment.yaml
deploy/README-k8s.md
```

1. Namespace `relay`. Every namespaced object sets it.
2. One `ConfigMap` for non-secret env: Kafka bootstrap, database name, `RELAY_METRICS_PORT=9100`, `AEDE_QUARANTINE_THRESHOLD`, feature flags matching Compose. Secrets (Postgres password, Redis password, `RELAY_WEBHOOK_SIGNING_SECRET`) come from `secret.yaml` placeholders (D7).
3. Kafka and Postgres are single-replica `StatefulSet`s with a `volumeClaimTemplate`. Redis is a `Deployment`. These are demo-grade, not a production cluster. The README says so.
4. `relay-api`, `relay-dispatcher`, `relay-dashboard`, `relay-simulator` use the same image and the same commands as Compose. API and dashboard and simulator expose their container ports. Dispatcher exposes `9100`.
5. Probes: HTTP `/docs` for API and simulator, `/api/chart/endpoints` for dashboard, matching Compose healthchecks. Dispatcher has no HTTP route except the metrics port, so readiness is TCP on `9100`. Liveness for the dispatcher is the same port. Person B confirms this when reviewing.
6. API CPU HPA, min 1 max 3. Dispatcher CPU HPA, min 1 max 3 (implemented value; ERI state is per process, see README-k8s), plus the KEDA `ScaledObject` (D6): `kafka` trigger, bootstrap from the ConfigMap, consumer group `relay-dispatcher`, lag threshold documented in the README. `pollingInterval` and `cooldownPeriod` set explicitly.
7. Ingress for the API only, host placeholder `relay.local`. No TLS secret checked in.
8. Prometheus `Deployment` mounts `deploy/prometheus.yml` via a ConfigMap you also generate instructions for in the README (the YAML file in git is the source; the README shows `kubectl create configmap` from it). Grafana `Deployment` mounts the datasource provider and the dashboards volume.
9. `deploy/README-k8s.md`: apply order, placeholder secret step, KEDA install note, the probe table (the dispatcher has none by design), and that these manifests are not applied as part of the demo script (`docker compose up` is).
10. Validate with `kubectl apply --dry-run=client -f deploy/k8s` and, if `kubeconform` is installed, `kubeconform -strict -summary deploy/k8s`. Do not require a running cluster.

Small decision: `docs/small-decisions-personA/k8s-demo-manifests.md` — single replica, CPU HPA plus KEDA lag, placeholders in `secret.yaml`.

### 4.4 T14 decision record

After 4.2 and 4.3. Write `docs/small-decisions-personA/repeat-endpoint-registration.md` choosing option 1 (D5). No code change. If the team later wants option 2, it is a new task with an Alembic migration that deduplicates `(owner_id, target_url)` before adding the unique constraint, and it must not alter historical delivery rows.

### 4.5 Review-only

- T9 and T12: already on `dev`. Re-read only if those files change under you.
- Person B's Grafana JSON and metric call sites: check that they import your collectors and call `start_metrics_server`, and that `relay.json` sits in the provisioned directory.

### 4.6 Diagrams — match the current tree

Update the three files in `docs/Diagrams/` in place. Do not add a fourth file. Do not draw the Razorpay-style overview here. That diagram is a separate pass, run only after this plan is finished.

These files still say "Weeks 1–4" and still show Strategy A as the only policy path. The tree on `dev` already has live AEDE, strategies A/B/C, replay, HMAC, the delivery adapter, and the dashboard feed. After 4.1–4.3, it also has `relay/kernel/events/`, Prometheus, Grafana, and `deploy/k8s/`. The diagrams must match that, not the old sprint.

`docs/Diagrams/use-case-diagram.md`
- Drop the "Weeks 1–4 / Strategy A only" frame. AEDE is the control actor and it is in scope.
- Add use cases that exist in code: select strategy from feature flags, sign the webhook (HMAC), park on `PAUSE` / `QUARANTINE`, replay from the DLQ (`events.internal.replay`), restore a quarantined endpoint, expose `/metrics`.
- Keep the same mermaid flowchart style.

`docs/Diagrams/data-plane-sequence-diagram.md`
- Ingest stays persist-first: Postgres, then `events.business`, then 202. Offset commit stays after Postgres.
- Policy selection is feature flags → Strategy A, B, or C, not a hard-coded `StrategyA.evaluate()`.
- Show HMAC headers on the worker POST, telemetry written for ERI, and the retry topic only on a retryable failure.
- Add the replay path: `POST /replay` → `events.internal.replay` → dispatcher. Original delivery rows are not updated.
- Add the dispatcher metrics port (`RELAY_METRICS_PORT`, default 9100) as a note, not a new participant on every arrow.

`docs/Diagrams/data-plane-class-diagram.md`
- Keep Event, Endpoint, Subscription, Delivery, DeliveryAttempt, Telemetry, DeliveryPolicy.
- Add the types that are actually in `relay/kernel/` now: ERI, endpoint state, explainability, and `DeliveryAdapter` only as a protocol name (no `httpx`).
- Add Person A's `InternalEvent` subclasses from section 3.2 once 4.1 has landed. Do not draw Person B's policy, endpoint, or replay event classes until those files exist.
- Status values stay `PENDING`, `SUCCESS`, `FAILED`, `DEAD_LETTER`. Mention parked `PENDING` (no attempt, skipped under PAUSE/QUARANTINE) in the prose under the diagram.

Read the code before changing a box. If a class or topic is not in the tree, leave it out.

---

## 5. Schedule

| Day | Work |
|---|---|
| 1 | 4.1 typed events and tests. Send section 3.2 to Person B. |
| 2 | 4.2 metrics module, dependency, API `/metrics`, unit tests. Send section 3.1 to Person B. |
| 3 | 4.2 Prometheus and Grafana Compose services, scrape config, provisioning, dispatcher metrics port. |
| 4 | 4.3 namespace, config, secret, Kafka, Postgres, Redis. |
| 5 | 4.3 API, dispatcher (HPA + KEDA), dashboard, simulator. |
| 6 | 4.3 Prometheus and Grafana manifests. `deploy/README-k8s.md`. Dry-run validation. |
| 7 | `docker compose up` and confirm Prometheus has an `api` target up. `dispatcher` stays down until Person B calls `start_metrics_server`. Record T14 (4.4) and D4. Update `docs/Diagrams/` (4.6). |
| 8 | Buffer. Fix manifest lint. Re-review B's emissions if they have landed. |

T13 is first because it is small and Person B's event modules wait on `base.py`. T7 is next because the demo script's Grafana minute depends on it and Person B's Day 5 wiring waits on `metrics.py`. T10 does not block the Compose demo.

---

## 6. What you need from others

**Person B**
- After 4.2: call `start_metrics_server()`, emit D1 from the worker and `AEDEService`, add `deploy/grafana/dashboards/relay.json`.
- After 4.1: add `policy_events.py`, `endpoint_events.py`, `replay_events.py` subclassing `InternalEvent`.
- Review the KEDA lag trigger and the dispatcher probes. The implemented manifest has no dispatcher probe (a commented block shows the TCP probe on 9100), because a metrics port alone does not prove the consume loop is alive.

**Person C**
- Point the WebSocket at `ws://127.0.0.1:8090/ws`.
- Run the frontend with Vite on port 5173. No Compose service is coming for it (D4).

---

## 7. Done criteria

Every step in section 7a has been run and its result reported.

- `pytest tests/unit/test_internal_events.py tests/unit/test_metrics.py` passes, and the existing suite still passes.
- `GET http://127.0.0.1:8000/metrics` returns Prometheus text after `docker compose up`.
- Prometheus UI shows the `api` target up. The `dispatcher` target is up once Person B has called `start_metrics_server`.
- Grafana on `127.0.0.1:3000` has the Prometheus datasource provisioned. It shows Person B's dashboard after `relay.json` exists, and an empty dashboard list before that.
- `kubectl apply --dry-run=client -f deploy/k8s` succeeds.
- `relay/kernel/events/` contains your three modules and `__init__.py`. Person B's three modules are absent until they land them.
- No edits under `relay/control_plane/`, `relay/dashboard/frontend/`, `simulator/`, or existing `relay/kernel/` modules outside `events/`.
- T14 is a decision note, not a migration.
- `docs/Diagrams/use-case-diagram.md`, `data-plane-sequence-diagram.md`, and `data-plane-class-diagram.md` describe the tree after 4.1–4.3. They no longer say the pipeline is Strategy A only.

---

## 7a. Testing — run before calling any item done

Run each step after its work item, not only at the end. Report the real output. If a step cannot run, say which one and why. Do not report it as passed.

Known hazards on `dev`:
- `tests/integration/test_kafka_pipeline.py`, `tests/integration/test_telemetry_loop.py` and `tests/unit/test_benchmark.py::test_validate_against_real_aede` need a running Kafka broker and hang without one.
- `tests/integration/test_end_to_end_adaptation.py` is flaky (unseeded failure rate). Re-run it once before treating a failure as yours.
- `docker compose` and `kubectl` may be missing on the machine. Check with `command -v` first.

| Step | When | Command | Pass means |
|---|---|---|---|
| T1 | After 4.1 | `pytest tests/unit/test_internal_events.py -q` | All pass: frozen, JSON round trip. |
| T2 | After 4.1 | `python -c "import relay.kernel.events as e; print(sorted(n for n in dir(e) if not n.startswith('_')))"` and `rg -n "httpx\|aiokafka\|fastapi\|sqlalchemy" relay/kernel/events` | Import works. The `rg` finds nothing. |
| T3 | After 4.2 | `pytest tests/unit/test_metrics.py -q` | All pass: D1 names and labels, `RELAY_METRICS_PORT`, second `start_metrics_server` call is safe. |
| T4 | After 4.2 | `pytest tests/unit tests/data_plane tests/dashboard tests/test_simulator.py -q --deselect tests/unit/test_benchmark.py::test_validate_against_real_aede` | Same pass count as before your changes. Nothing new fails. |
| T5 | After 4.2 | `docker compose config -q` | Exit 0. YAML is valid. |
| T6 | After 4.2 | `docker compose up -d --build` then `curl -fsS http://127.0.0.1:8000/metrics \| head`, `curl -fsS http://127.0.0.1:9090/-/ready`, `curl -fsS http://127.0.0.1:3000/api/health` | `/metrics` returns Prometheus text. Prometheus and Grafana answer. |
| T7 | After 4.2 | `curl -fsS http://127.0.0.1:9090/api/v1/targets` | `api` is `up`. `dispatcher` is `down` until Person B calls `start_metrics_server`. Write that down. It is expected, not a failure. |
| T8 | After 4.2 | `curl -fsS -u admin:<password> http://127.0.0.1:3000/api/datasources` | The Prometheus datasource exists. |
| T9 | After 4.2, Kafka up | `pytest tests/integration -q` | Passes. Re-run `test_end_to_end_adaptation.py` once if it fails. |
| T10 | After 4.3 | `kubectl apply --dry-run=client -f deploy/k8s -R` | Exit 0. If `kubectl` is missing, run `python -c "import yaml,glob;[list(yaml.safe_load_all(open(f))) for f in glob.glob('deploy/k8s/**/*.yaml',recursive=True)]"` and say that only YAML syntax was checked. |
| T11 | After 4.3 | `kubeconform -strict -summary -ignore-missing-schemas deploy/k8s` | Zero invalid. Skip, and say so, if it is not installed. The KEDA `ScaledObject` needs `-ignore-missing-schemas`. |
| T12 | After 4.6 | For each mermaid block in `docs/Diagrams/*.md`: `npx -y @mermaid-js/mermaid-cli -i <file> -o /tmp/<name>.svg` | Every block renders. If `npx` is unavailable, re-read each block for syntax and say it was not rendered. |
| T13 | End | `pytest tests -q --deselect tests/unit/test_benchmark.py::test_validate_against_real_aede` with Kafka up, then `docker compose down` | Whole suite green. Report the count against the 436 baseline from `docs/demo_implementation_plan.md`. |
| T14 | End | `git status -sb` and `git diff --stat dev` | Only files listed in section 4. Nothing under the section 7 "no edits" paths. |

Failure rule: if a step fails, fix the cause and re-run that step. Do not delete or skip a test to get a pass, and do not weaken an assertion.

---

## 8. Out of scope

- Metric emission inside the worker, AEDE, or the Grafana panel JSON (Person B).
- `policy_events.py`, `endpoint_events.py`, `replay_events.py` (Person B).
- Replacing Kafka payload schemas with the new event types.
- A Compose service for the React build (D4).
- Option 2 of T14.
- Redis three-tier changes, Terraform, OpenTelemetry, extra delivery adapters, Alertmanager, a real cluster apply.
- Editing ADR 0001–0021. New ADRs start at 0022, and this plan does not need one. The decisions in section 2 are small decisions.
