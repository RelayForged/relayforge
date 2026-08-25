from relay.dashboard.adapter import (
    build_state_update,
    create_eri_point,
    get_delivery_events,
    get_policy_decision,
    get_policy_explanation,
)
from relay.dashboard.demo_source import DashboardDemoSource


def test_create_eri_point_has_dashboard_contract() -> None:
    point = create_eri_point(82.7)

    assert point["value"] == 82
    assert isinstance(point["timestamp"], str)


def test_get_delivery_events_returns_empty_until_data_plane_is_connected() -> None:
    assert get_delivery_events() == []


def test_policy_decision_contains_full_dashboard_fields() -> None:
    source = DashboardDemoSource()

    _, state, policy, _ = source.next_evaluation()

    decision = get_policy_decision(policy)

    assert decision["policy_id"] == str(policy.policy_id)
    assert decision["endpoint_id"] == source.endpoint_id
    assert decision["endpoint_state"] == state.value
    assert "max_parallel" in decision
    assert "queue_priority" in decision
    assert "max_retries" in decision
    assert "backoff_strategy" in decision
    assert "isolation_action" in decision


def test_policy_explanation_contains_state_transition_information() -> None:
    source = DashboardDemoSource()

    _, _, policy, explanation = source.next_evaluation()

    result = get_policy_explanation(explanation)

    assert result["record_id"] == str(explanation.record_id)
    assert result["policy_id"] == str(policy.policy_id)
    assert result["endpoint_id"] == source.endpoint_id
    assert "state_before" in result
    assert "state_after" in result
    assert "state_changed" in result
    assert "strategy_used" in result


def test_build_state_update_has_complete_dashboard_sections() -> None:
    source = DashboardDemoSource()

    eri, state, policy, explanation = source.next_evaluation()

    update = build_state_update(
        state=state.value,
        eri=eri,
        policy=policy,
        explanation=explanation,
    )

    assert update["type"] == "state_update"
    assert update["state"] == state.value
    assert update["eri"] == eri

    assert "eri_history" in update
    assert "delivery_events" in update
    assert "policy_decision" in update
    assert "policy_explanation" in update

    assert update["policy_decision"]["policy_id"] == str(policy.policy_id)
    assert update["policy_explanation"]["record_id"] == str(
        explanation.record_id
    )