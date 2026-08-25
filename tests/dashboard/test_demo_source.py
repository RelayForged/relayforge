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