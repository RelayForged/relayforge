"""Prometheus metrics module: contract names/labels, port handling, exporter."""
import socket
import urllib.request

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from relay.infrastructure.monitoring import metrics


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _reset_server_state(monkeypatch):
    """Each test starts with no exporter bound and no port env set."""
    monkeypatch.setattr(metrics, "_bound_port", None)
    monkeypatch.delenv(metrics.METRICS_PORT_ENV, raising=False)


def _label_dicts(sample_name: str) -> list[dict]:
    """Full label dicts (names and values) of every sample with this name."""
    return [
        dict(sample.labels)
        for family in REGISTRY.collect()
        for sample in family.samples
        if sample.name == sample_name
    ]


def _sample_labels(sample_name: str) -> set[frozenset]:
    """Label-name sets only, for checking a collector's label contract."""
    return {frozenset(labels) for labels in _label_dicts(sample_name)}


# --- contract: names and label sets (plan D1) --------------------------------

def test_delivery_attempts_counter_contract():
    metrics.DELIVERY_ATTEMPTS.labels(endpoint_id="ep-contract", status="success").inc()
    assert frozenset({"endpoint_id", "status"}) in _sample_labels("relay_delivery_attempts_total")


def test_eri_gauge_contract():
    metrics.ERI_SCORE.labels(endpoint_id="ep-contract").set(88.5)
    assert frozenset({"endpoint_id"}) in _sample_labels("relay_eri_score")


def test_state_gauge_has_state_label_only_not_transition_labels():
    metrics.ENDPOINT_STATE.labels(endpoint_id="ep-contract", state="AVAILABLE").set(1)
    labels = _sample_labels("relay_endpoint_state")
    assert frozenset({"endpoint_id", "state"}) in labels
    assert not any({"from_state", "to_state"} & set(l) for l in labels)


def test_transition_counter_contract():
    metrics.STATE_TRANSITIONS.labels(
        endpoint_id="ep-contract", from_state="AVAILABLE", to_state="UNSTABLE"
    ).inc()
    assert frozenset({"endpoint_id", "from_state", "to_state"}) in _sample_labels(
        "relay_endpoint_state_transitions_total"
    )


def test_retry_histogram_has_no_labels_and_contract_buckets():
    metrics.RETRY_COUNT.observe(3)
    buckets = _label_dicts("relay_delivery_retries_bucket")
    assert all(set(labels) == {"le"} for labels in buckets)
    assert {labels["le"] for labels in buckets} == {"1.0", "2.0", "4.0", "8.0", "16.0", "+Inf"}


def test_attempt_statuses_match_contract():
    assert metrics.ATTEMPT_STATUSES == ("success", "http_error", "timeout", "transport_error")


# --- port resolution ---------------------------------------------------------

def test_default_port_is_9100():
    assert metrics._resolve_port(None) == 9100


def test_port_read_from_env(monkeypatch):
    monkeypatch.setenv(metrics.METRICS_PORT_ENV, "9234")
    assert metrics._resolve_port(None) == 9234


def test_explicit_port_beats_env(monkeypatch):
    monkeypatch.setenv(metrics.METRICS_PORT_ENV, "9234")
    assert metrics._resolve_port(9555) == 9555


def test_bad_env_port_is_rejected(monkeypatch):
    monkeypatch.setenv(metrics.METRICS_PORT_ENV, "not-a-port")
    with pytest.raises(ValueError, match=metrics.METRICS_PORT_ENV):
        metrics.start_metrics_server()


# --- exporter ----------------------------------------------------------------

def test_start_reads_env_and_second_call_is_a_noop(monkeypatch):
    calls = []
    monkeypatch.setattr(metrics, "start_http_server", lambda port, registry: calls.append(port))
    monkeypatch.setenv(metrics.METRICS_PORT_ENV, "9321")

    assert metrics.start_metrics_server() == 9321
    # A second call, even with a different port, binds nothing new.
    assert metrics.start_metrics_server(9999) == 9321
    assert calls == [9321]


def test_exporter_serves_collectors_over_http():
    port = _free_port()
    assert metrics.start_metrics_server(port) == port
    metrics.ERI_SCORE.labels(endpoint_id="ep-http").set(42.0)

    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
    assert 'relay_eri_score{endpoint_id="ep-http"} 42.0' in body


# --- API /metrics ------------------------------------------------------------

def test_api_exposes_metrics_route():
    from relay.api.main import app

    # No `with`: skips the lifespan, so no Kafka producer is started.
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in response.text
    assert "http_request_duration_seconds" in response.text


def test_api_metrics_do_not_break_invalid_content_length():
    """The instrumentator must not parse Content-Length: the body-size
    middleware owns rejecting a malformed value with a 400."""
    from relay.api.main import app

    client = TestClient(app, raise_server_exceptions=True)
    response = client.post(
        "/events",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "abc"},
    )
    assert response.status_code == 400
    # And no request-size series that would read the header.
    text = client.get("/metrics").text
    assert "http_request_size_bytes" not in text
