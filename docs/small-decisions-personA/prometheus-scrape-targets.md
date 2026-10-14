# Decision: Two Prometheus scrape targets, one shared registry per process

- **Context:** T7 adds metrics, Prometheus and Grafana. The delivery, ERI and state series are produced inside the dispatcher process, which is a Kafka consumer with no HTTP server. The API is a separate process.

- **Decision 1 — Two scrape targets:** Prometheus scrapes `api:8000/metrics` (HTTP request count and latency from `prometheus-fastapi-instrumentator`) and `dispatcher:9100/metrics` (delivery attempts, ERI, endpoint state, retries). The dispatcher's port comes from `start_metrics_server()` in `relay/infrastructure/monitoring/metrics.py`, default 9100, override `RELAY_METRICS_PORT`.
  - **Why:** the API's `/metrics` cannot show series that live in another process. A second target is cheaper than moving the metrics into the API or adding an HTTP server to the dispatcher for any other reason.

- **Decision 2 — Transition Counter plus a state Gauge:** `relay_endpoint_state_transitions_total{endpoint_id, from_state, to_state}` counts changes; `relay_endpoint_state{endpoint_id, state}` is `1` on the active state and `0` on the previous one.
  - **Why:** a Gauge is a current value. The earlier idea of a Gauge labelled `from_state, to_state` cannot represent "it changed from X to Y". The Counter carries the change, the Gauge carries where the endpoint is now.

- **Decision 3 — Collectors live in prometheus_client's default registry:** `metrics.py` defines the collectors and starts the exporter; it emits nothing. The worker and AEDE call the collectors and the dispatcher calls `start_metrics_server()` (Person B).
  - **Why:** inside one process the API's instrumentator and this module then share one registry and cannot split series across two. The API and the dispatcher are separate processes, so each exports only what it emits.

- **Decision 4 — Register only the request-count and latency HTTP metrics:** the API uses `metrics.requests()` and `metrics.latency()`, not the library defaults.
  - **Why:** the default request-size metric does `int(headers["Content-Length"])`. With the instrumentator installed that raised on a malformed header and turned `BodySizeLimitMiddleware`'s 400 into an unhandled error (`tests/data_plane/test_api_security.py::test_invalid_content_length_is_rejected` caught it). Neither chosen metric reads that header, and a regression test keeps it that way.

- **Dispatcher target is down until the call is wired:** nothing starts the dispatcher's exporter yet, so Prometheus reports the `dispatcher` target as down. That is expected, not a fault; it goes up once Person B calls `start_metrics_server()`.

- **Grafana:** this task provisions the datasource (fixed uid `relay-prometheus`) and a file provider that loads `deploy/grafana/dashboards`. It draws no panels; `relay.json` is Person B's.
