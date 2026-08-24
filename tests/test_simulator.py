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