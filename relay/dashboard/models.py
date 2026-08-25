from typing import NotRequired, TypedDict


class ERIPoint(TypedDict):
    timestamp: str
    value: int


class DeliveryEvent(TypedDict):
    id: str
    timestamp: str
    type: str

    event_id: NotRequired[str]
    delivery_id: NotRequired[str]
    attempt_id: NotRequired[str]
    status: NotRequired[str]
    attempt_number: NotRequired[int]
    http_status: NotRequired[int | None]
    success: NotRequired[bool]
    error_message: NotRequired[str | None]


class PolicyDecision(TypedDict):
    decision: str
    selected_target: str
    reason: str

    policy_id: NotRequired[str]
    endpoint_id: NotRequired[str]
    endpoint_state: NotRequired[str]
    eri_score: NotRequired[float]
    computed_at: NotRequired[str]

    max_parallel: NotRequired[int]
    queue_priority: NotRequired[int]
    throughput_limit: NotRequired[int | None]

    isolation_action: NotRequired[str]

    max_retries: NotRequired[int]
    backoff_strategy: NotRequired[str]
    initial_delay_seconds: NotRequired[float]
    max_delay_seconds: NotRequired[float]


class PolicyExplanation(TypedDict):
    title: str
    reasons: list[str]

    record_id: NotRequired[str]
    policy_id: NotRequired[str]
    endpoint_id: NotRequired[str]
    state_before: NotRequired[str]
    state_after: NotRequired[str]
    state_changed: NotRequired[bool]
    strategy_used: NotRequired[str]
    evaluated_at: NotRequired[str]
    window_size: NotRequired[int]

    triggered_thresholds: NotRequired[list[str]]
    policy_changes: NotRequired[list[str]]

    metrics: NotRequired[dict[str, float]]
    thresholds: NotRequired[dict[str, float]]


class StateUpdate(TypedDict):
    type: str
    state: str
    eri: int
    eri_history: list[ERIPoint]
    delivery_events: list[DeliveryEvent]
    policy_decision: PolicyDecision
    policy_explanation: PolicyExplanation