from typing import TypedDict


class ERIPoint(TypedDict):
    timestamp: str
    value: int


class DeliveryEvent(TypedDict):
    id: int
    timestamp: str
    type: str


class PolicyDecision(TypedDict):
    decision: str
    selected_target: str
    reason: str


class PolicyExplanation(TypedDict):
    title: str
    reasons: list[str]


class StateUpdate(TypedDict):
    type: str
    state: str
    eri: int
    eri_history: list[ERIPoint]
    delivery_events: list[DeliveryEvent]
    policy_decision: PolicyDecision
    policy_explanation: PolicyExplanation