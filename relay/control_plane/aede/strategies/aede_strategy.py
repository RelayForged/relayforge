"""
Strategy C — Full AEDE Delivery Strategy.

Combines the Endpoint State Machine (with hysteresis) and the Policy Synthesizer.
This is the core research contribution of the platform.
"""

from datetime import datetime
from typing import Tuple

from relay.control_plane.aede.synthesizer import synthesize_policy
from relay.control_plane.explainability.record import ExplainabilityRecord
from relay.control_plane.state_machine.transitions import next_state
from relay.kernel.contracts.delivery_strategy import DeliveryStrategy
from relay.kernel.policies.delivery_policy import DeliveryPolicy
from relay.kernel.value_objects.endpoint_state import (
    EndpointState,
    StateMachineThresholds,
)
from relay.kernel.value_objects.eri import ERIScore


class StrategyC(DeliveryStrategy):
    """
    Strategy C (AEDE).
    Runs state machine transition logic and synthesizes policy accordingly.
    """

    def __init__(self, thresholds: StateMachineThresholds | None = None):
        self.thresholds = thresholds or StateMachineThresholds(
            degradation_threshold=80.0,
            degradation_recovery_threshold=90.0,
            unstable_threshold=60.0,
            unstable_recovery_threshold=70.0,
            quarantine_threshold=30.0,
            recovery_threshold=90.0,
        )

    @property
    def strategy_name(self) -> str:
        return "STRATEGY_C"

    def evaluate(
        self,
        endpoint_id: str,
        endpoint_state: EndpointState,
        eri: ERIScore,
        computed_at: datetime,
    ) -> Tuple[DeliveryPolicy, ExplainabilityRecord]:
        state_after = next_state(endpoint_state, eri, self.thresholds)
        policy, record = synthesize_policy(
            endpoint_id=endpoint_id,
            state_before=endpoint_state,
            state_after=state_after,
            eri=eri,
            thresholds=self.thresholds,
            computed_at=computed_at,
        )
        return policy, record
