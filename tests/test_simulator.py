from simulator.config import FailureMode, SimulatorConfig
from simulator.service import simulate_webhook


def test_http_error_mode_returns_500():
    config = SimulatorConfig(
        mode=FailureMode.HTTP_ERROR,
        rate=1.0,
    )

    response = simulate_webhook(config)

    assert response.status_code == 500
    assert response.body == b"Simulated HTTP error"

def test_rate_limit_mode_returns_429():
    config = SimulatorConfig(
        mode=FailureMode.RATE_LIMIT,
        rate=1.0,
    )

    response = simulate_webhook(config)

    assert response.status_code == 429
    assert response.body == b"Simulated rate limit"


def test_slow_mode_returns_200():
    config = SimulatorConfig(
        mode=FailureMode.SLOW,
        rate=1.0,
        latency_ms=10,
    )

    response = simulate_webhook(config)

    assert response.status_code == 200
    assert response.body == b"Webhook delivered successfully"


def test_timeout_mode_returns_200_after_delay():
    config = SimulatorConfig(
        mode=FailureMode.TIMEOUT,
        rate=1.0,
        latency_ms=10,
    )

    response = simulate_webhook(config)

    assert response.status_code == 200
    assert response.body == b"Webhook delivered successfully"

def test_zero_rate_always_succeeds():
    config = SimulatorConfig(
        mode=FailureMode.HTTP_ERROR,
        rate=0.0,
    )

    response = simulate_webhook(config)

    assert response.status_code == 200
    assert response.body == b"Webhook delivered successfully"

def test_success_mode_ignores_rate():
    config = SimulatorConfig(mode=FailureMode.SUCCESS, rate=1.0)

    response = simulate_webhook(config)

    assert response.status_code == 200


def test_auth_failure_mode_returns_401():
    config = SimulatorConfig(mode=FailureMode.AUTH_FAILURE, rate=1.0)

    response = simulate_webhook(config)

    assert response.status_code == 401
    assert response.body == b"Simulated authentication failure"


def test_mixed_mode_yields_errors_and_slow_successes():
    config = SimulatorConfig(mode=FailureMode.MIXED, rate=1.0, latency_ms=0)

    statuses = {simulate_webhook(config).status_code for _ in range(200)}

    assert statuses == {200, 500}


def test_connection_reset_mode_returns_reset_response():
    from simulator.service import ConnectionResetResponse

    config = SimulatorConfig(mode=FailureMode.CONNECTION_RESET, rate=1.0)

    assert isinstance(simulate_webhook(config), ConnectionResetResponse)


def test_connection_reset_falls_back_to_502_without_a_transport():
    from fastapi.testclient import TestClient

    from simulator.main import app, simulator_state

    client = TestClient(app)
    previous = simulator_state.config
    try:
        client.post(
            "/simulator/config",
            json={"mode": "connection_reset", "rate": 1.0},
        )
        assert client.get("/simulator/config").json()["mode"] == "connection_reset"
        assert client.post("/webhook").status_code == 502
    finally:
        simulator_state.config = previous
