from relay.dashboard.demo_source import DashboardDemoSource
from relay.kernel.value_objects.endpoint_state import EndpointState


def test_demo_source_eri_changes_from_initial_value() -> None:
    source = DashboardDemoSource()

    scores = [source.next_evaluation()[0] for _ in range(15)]

    assert len(set(scores)) > 1


def test_demo_source_reaches_degraded_state() -> None:
    source = DashboardDemoSource()

    states = [source.next_evaluation()[1] for _ in range(40)]

    assert EndpointState.OBSERVED_DEGRADATION in states
    assert EndpointState.UNSTABLE in states


def test_demo_source_reaches_quarantined_state() -> None:
    source = DashboardDemoSource()

    states = [source.next_evaluation()[1] for _ in range(80)]

    assert EndpointState.QUARANTINED in states


def test_demo_source_produces_policy_and_explanation() -> None:
    source = DashboardDemoSource()

    for _ in range(80):
        score, state, policy, explanation = source.next_evaluation()

        assert score >= 0
        assert state in EndpointState
        assert policy.endpoint_id == source.endpoint_id
        assert explanation.endpoint_id == source.endpoint_id

def test_demo_source_uses_telemetry_for_evaluation() -> None:
    source = DashboardDemoSource()

    assert source.attempt_number == 0
    assert source.attempts == []

    source.next_evaluation()

    assert source.attempt_number == 1
    assert len(source.attempts) == 1

    telemetry = source.attempts[0]

    assert telemetry.endpoint_id == source.endpoint_id
    assert telemetry.attempt_id == "dashboard-demo-attempt-1"


def test_demo_source_records_sustained_failures() -> None:
    source = DashboardDemoSource()

    for _ in range(30):
        source.next_evaluation()

    failures = [attempt for attempt in source.attempts if not attempt.succeeded]

    assert failures
    assert len(failures) >= 15
    assert any(attempt.timed_out for attempt in failures)
    assert any(attempt.http_status == 500 for attempt in failures)

def test_demo_source_accepts_fault_injection_configuration() -> None:
    source = DashboardDemoSource(
        failure_rate=0.5,
        latency_ms=750.0,
        failure_mode="timeout",
    )

    assert source.failure_rate == 0.5
    assert source.latency_ms == 750.0
    assert source.failure_mode == "timeout"


def test_demo_source_rejects_invalid_fault_injection_configuration() -> None:
    try:
        DashboardDemoSource(failure_rate=1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected invalid failure_rate to raise")


def test_demo_source_rejects_unknown_failure_mode() -> None:
    try:
        DashboardDemoSource(failure_mode="unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected invalid failure_mode to raise")

def test_timeout_failure_mode_generates_timeouts() -> None:
    source = DashboardDemoSource(
        failure_rate=1.0,
        failure_mode="timeout",
    )

    for _ in range(20):
        source.next_evaluation()

    failures = [
        attempt
        for attempt in source.attempts
        if not attempt.succeeded
    ]

    assert failures
    assert all(attempt.timed_out for attempt in failures)
    assert all(attempt.http_status is None for attempt in failures)


def test_http_500_failure_mode_generates_http_errors() -> None:
    source = DashboardDemoSource(
        failure_rate=1.0,
        failure_mode="http_500",
    )

    for _ in range(20):
        source.next_evaluation()

    failures = [
        attempt
        for attempt in source.attempts
        if not attempt.succeeded
    ]

    assert failures
    assert all(not attempt.timed_out for attempt in failures)
    assert all(attempt.http_status == 500 for attempt in failures)


def test_zero_failure_rate_stays_healthy() -> None:
    source = DashboardDemoSource(
        failure_rate=0.0,
        latency_ms=150.0,
        failure_mode="timeout",
    )

    for _ in range(50):
        source.next_evaluation()

    assert len(source.attempts) == 50
    assert all(attempt.succeeded for attempt in source.attempts)
    assert all(not attempt.timed_out for attempt in source.attempts)
    assert all(attempt.http_status == 200 for attempt in source.attempts)