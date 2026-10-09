# Relay End-to-End Live Demonstration

This directory contains the automated end-to-end demonstration script (`run_demo.py`) that exercises the complete Relay event delivery platform over live Kafka, PostgreSQL, Redis, and HTTP networks.

---

## Overview of the Live Demo

The demo proves that Relay treats webhook delivery as a distributed systems problem rather than a simple HTTP POST:

1. **Pre-flight Health**: Verifies that API (`http://localhost:8000`) and Simulator (`http://localhost:8080`) are healthy.
2. **Registration**: Registers a destination endpoint pointing to `http://simulator:8080/webhook` and creates a subscription for `order.created` events.
3. **Phase 1 (Nominal Traffic)**: Publishes nominal events. The simulator responds with HTTP 200 OK. ERI stays at ~100 and state remains `AVAILABLE`.
4. **Phase 2 (Fault Injection)**: Configures the simulator to return HTTP 500 errors. Publishes events through Kafka. Webhook workers record failures, feeding telemetry into AEDE. AEDE adapts in real time, transitioning:
   `AVAILABLE -> OBSERVED_DEGRADATION -> UNSTABLE -> QUARANTINED`.
5. **Phase 3 (Quarantine Enforcement)**: Publishes events while quarantined. Verifies that events are safely buffered in durable Kafka storage and zero HTTP delivery attempts leak to the failing endpoint.
6. **Phase 4 (Restoration & Recovery)**: Removes the fault from the simulator and issues an operator restore command (`POST /endpoints/{id}/restore`). AEDE transitions to `RECOVERING` and sends serialized probe traffic. Upon successive successes, AEDE returns the endpoint to `AVAILABLE`.
7. **Summary Report**: Prints a formatted summary table displaying event counts, timing, and state machine transitions.

---

## Prerequisites

Start the entire platform using Docker Compose:

```bash
docker compose up -d --build
```

Verify that all services are running:

```bash
docker compose ps
```

---

## Running the Demo

Run the automated demo script:

```bash
python demo/run_demo.py
```

### CLI Options

| Flag | Description | Default |
|---|---|---|
| `--api-url` | Relay Ingestion API base URL | `http://localhost:8000` |
| `--simulator-url` | Endpoint Simulator control URL | `http://localhost:8080` |
| `--target-url` | Destination webhook URL as seen by Dispatcher | `http://simulator:8080/webhook` |
| `--quick` | Run with reduced event counts for faster verification | `False` |

Example for quick execution:
```bash
python demo/run_demo.py --quick
```

---

## Tear Down

When finished, shut down the platform:

```bash
docker compose down -v
```
