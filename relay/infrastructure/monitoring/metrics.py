"""
Prometheus collectors for the delivery pipeline (T7).

This module only *defines* the collectors and starts an exporter. It emits
nothing: the worker and AEDE call these collectors (Person B), and the
dispatcher process calls `start_metrics_server()` once at startup. The
dispatcher has no HTTP server of its own, so its series are exported on a
dedicated port (`RELAY_METRICS_PORT`, default 9100) and scraped separately
from the API's `/metrics`.

Collectors live in prometheus_client's default registry. The API's
instrumentator and this module therefore share one registry inside a process
and cannot split series between two. The API and the dispatcher are separate
processes, so each exports only what it emits.

Label contract (docs/person_a_implementation_plan.md, D1):

    relay_delivery_attempts_total{endpoint_id, status}
        status: success | http_error | timeout | transport_error
    relay_eri_score{endpoint_id}                      latest ERI, 0-100
    relay_endpoint_state{endpoint_id, state}          1 = current, 0 = previous
    relay_endpoint_state_transitions_total{endpoint_id, from_state, to_state}
    relay_delivery_retries                            attempt number at terminal status

A gauge holds a current value, so a transition cannot live in
`relay_endpoint_state`: the transition Counter carries from/to, the gauge
carries only which state is active.
"""
import os
import threading

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, start_http_server

DEFAULT_METRICS_PORT = 9100
METRICS_PORT_ENV = "RELAY_METRICS_PORT"

# status values the worker should use for DELIVERY_ATTEMPTS.
ATTEMPT_STATUSES = ("success", "http_error", "timeout", "transport_error")

DELIVERY_ATTEMPTS = Counter(
    "relay_delivery_attempts_total",
    "Completed delivery attempts by endpoint and outcome.",
    ["endpoint_id", "status"],
)

ERI_SCORE = Gauge(
    "relay_eri_score",
    "Latest Endpoint Reliability Index (0-100) per endpoint.",
    ["endpoint_id"],
)

ENDPOINT_STATE = Gauge(
    "relay_endpoint_state",
    "Current endpoint state: 1 on the active state, 0 on the previous one.",
    ["endpoint_id", "state"],
)

STATE_TRANSITIONS = Counter(
    "relay_endpoint_state_transitions_total",
    "Endpoint state transitions.",
    ["endpoint_id", "from_state", "to_state"],
)

RETRY_COUNT = Histogram(
    "relay_delivery_retries",
    "Attempt number observed when a delivery reaches a terminal status.",
    buckets=(1, 2, 4, 8, 16),
)

_server_lock = threading.Lock()
_bound_port: int | None = None


def _resolve_port(port: int | None) -> int:
    if port is not None:
        return port
    raw = os.environ.get(METRICS_PORT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_METRICS_PORT
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{METRICS_PORT_ENV} must be an integer port, got {raw!r}") from exc


def start_metrics_server(port: int | None = None) -> int:
    """
    Serve the registry over HTTP on `port` and return the bound port.

    Port comes from the argument, else `RELAY_METRICS_PORT`, else 9100.
    Idempotent within a process: a second call returns the port already bound
    and starts nothing, whatever port it was asked for.
    """
    global _bound_port
    with _server_lock:
        if _bound_port is not None:
            return _bound_port
        resolved = _resolve_port(port)
        start_http_server(resolved, registry=REGISTRY)
        _bound_port = resolved
        return resolved
