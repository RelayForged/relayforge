# Full-Stack Containerised Integration & End-to-End Demo

## 1. Executive Summary & Objective

The objective of this plan is to containerise the Relay application services and integrate them into `docker-compose.yml` so that `docker compose up` brings up the **entire platform** — infrastructure (Kafka, PostgreSQL, Redis) and application services (API, Dispatcher, Simulator, Migrator) — in one command. This closes the gap identified in the project critique by validating the complete architecture over live Kafka, PostgreSQL, Redis, and HTTP networks.

Furthermore, this plan delivers:
1. An automated end-to-end demo script (`demo/run_demo.py`) that drives live traffic through Kafka, injects HTTP faults into the simulator, verifies AEDE closed-loop state transitions (`AVAILABLE -> OBSERVED_DEGRADATION -> UNSTABLE -> QUARANTINED -> RECOVERING -> AVAILABLE`), tests quarantine isolation, and verifies manual restoration.
2. An admin restoration API endpoint (`POST /endpoints/{id}/restore`) enabling manual recovery from quarantine.
3. Closed-loop AEDE telemetry wiring in the standalone Dispatcher process.
4. A bursty event publication scenario (Scenario 11) in the benchmark suite evaluating ERI sliding window responsiveness under non-uniform traffic bursts.
5. Complete documentation updates across `docs/report/04-evaluation-methodology.md` and repository checklists.

---

## 2. Core Acceptance Criteria

- [ ] **AC-1 (Application Dockerfile)**: Multi-stage Python 3.11 Dockerfile (`Dockerfile`) capable of running `api`, `dispatcher`, `simulator`, and `migrator` with all required dependencies.
- [ ] **AC-2 (Full-Stack Docker Compose Integration)**: `docker-compose.yml` extended with `simulator`, `api`, `dispatcher`, and `migrator` services, properly configured with internal Docker network hostnames (`kafka:29092`, `postgres:5432`, `redis:6379`), environment variables, and healthcheck dependencies.
- [ ] **AC-3 (Live AEDE Closed-Loop Feedback & Admin Restore API)**: Dispatcher wires `AEDEService` into `WebhookWorker` so live attempt completions trigger closed-loop ERI calculation, state machine transitions, and policy updates; API exposes `POST /endpoints/{endpoint_id}/restore` to transition endpoints from QUARANTINED to RECOVERING.
- [ ] **AC-4 (End-to-End Demo Script)**: `demo/run_demo.py` executes against the live platform: registers endpoint, creates subscription, publishes nominal events, injects HTTP 500 failure, verifies degradation and quarantine with zero HTTP attempts during quarantine, calls admin restore, publishes recovery events, verifies recovery to AVAILABLE, and prints a formatted execution report.
- [ ] **AC-5 (Scenario 11 Bursty Traffic Benchmark)**: `SCENARIO_11_BURSTY_TRAFFIC` added to `benchmark/scenarios.py`, evaluated in `benchmark/run_experiments.py`, and verified in `tests/unit/test_benchmark.py`.
- [ ] **AC-6 (Evaluation Report & Documentation Update)**: `docs/report/04-evaluation-methodology.md` updated with §4.14.9 (Full-Stack Containerised Integration Results) and Scenario 11 data; `walkthrough.md` updated with comprehensive validation summary.

---

## 3. Work Breakdown & Tasks

### Task 1: Application Dockerfile & Docker Compose Service Extensions
- **Files**:
  - `Dockerfile` [NEW]
  - `.dockerignore` [NEW]
  - `docker-compose.yml` [MODIFY]
- **Scope**:
  - Create Python 3.11 multi-stage Dockerfile installing project and dependencies.
  - Create `.dockerignore` ignoring virtual environments, caches, `.git`, `.agent`.
  - Extend `docker-compose.yml` with `simulator`, `api`, `dispatcher`, `migrator` services.
  - Configure networking: `kafka:29092`, `postgres:5432`, `redis:6379`.
- **Criteria**: Maps to AC-1, AC-2.

### Task 2: Dispatcher AEDE Closed-Loop Wiring & Admin Restore API Endpoint
- **Files**:
  - `relay/data_plane/dispatcher/main.py` [MODIFY]
  - `relay/api/endpoints.py` [MODIFY]
  - `relay/api/schemas.py` [MODIFY]
  - `tests/unit/test_api.py` [MODIFY]
  - `tests/unit/test_dispatcher.py` [MODIFY]
- **Scope**:
  - In `relay/data_plane/dispatcher/main.py`, instantiate `AEDEService` when `aede_enabled` is True and wire into `WebhookWorker`.
  - In `relay/api/endpoints.py`, add `POST /endpoints/{endpoint_id}/restore` endpoint to allow operators to restore an endpoint from QUARANTINED.
  - Add unit tests for the restore API and dispatcher wiring.
- **Criteria**: Maps to AC-3.

### Task 3: Automated End-to-End Live Demo Script
- **Files**:
  - `demo/run_demo.py` [NEW]
  - `demo/README.md` [NEW]
  - `tests/integration/test_e2e_demo_flow.py` [NEW]
- **Scope**:
  - Implement `demo/run_demo.py` supporting CLI flags (`--api-url`, `--simulator-url`).
  - Implements full 5-stage live demonstration flow:
    1. Endpoint & Subscription setup
    2. Phase 1: Nominal delivery (20 events)
    3. Phase 2: Fault injection (HTTP 500s) leading to degradation and quarantine
    4. Phase 3: Quarantine verification (events withheld, 0 HTTP leaks)
    5. Phase 4: Fault removal, admin restoration (`POST /endpoints/{id}/restore`), recovery phase
    6. Phase 5: Return to AVAILABLE state and formatted summary printout
  - Add integration test exercising the demo sequence logic with mocked or local servers.
- **Criteria**: Maps to AC-4.

### Task 4: Scenario 11 Bursty Traffic Benchmark & Sensitivity Updates
- **Files**:
  - `benchmark/scenarios.py` [MODIFY]
  - `benchmark/run_experiments.py` [MODIFY]
  - `tests/unit/test_benchmark.py` [MODIFY]
- **Scope**:
  - Define `SCENARIO_11_BURSTY_TRAFFIC` in `benchmark/scenarios.py` with 5 phases testing burst publication under degradation.
  - Update benchmark experiment runner and unit tests to validate Scenario 11.
- **Criteria**: Maps to AC-5.

### Task 5: Evaluation Report, Documentation & Acceptance Audit
- **Files**:
  - `docs/report/04-evaluation-methodology.md` [MODIFY]
  - `PLAN.md` [MODIFY]
  - `walkthrough.md` [MODIFY]
- **Scope**:
  - Add §4.14.9 to `docs/report/04-evaluation-methodology.md` documenting the containerised architecture, live demo logs, and Scenario 11 results.
  - Ensure all 306+ unit and integration tests pass cleanly.
  - Final Foreman acceptance audit.
- **Criteria**: Maps to AC-6.
