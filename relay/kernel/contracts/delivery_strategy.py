"""
DeliveryStrategy — the abstract contract that all delivery strategies implement.

The platform supports three delivery strategies, switchable via feature flags:

    Strategy A (Static):    Fixed exponential backoff. No state awareness.
                            Also serves as Tier 3 fallback when AEDE is unavailable.

    Strategy B (Adaptive):  Adjusts retry intervals based on recent success rate.
                            No state machine, no quarantine. Research baseline.

    Strategy C (AEDE):      Full adaptive engine. Uses ERI, State Machine, and
                            Policy Synthesizer. The research contribution.

All three strategies produce the same output type (DeliveryPolicy), so the
Dispatcher and Workers are completely strategy-agnostic. The active strategy
is selected by feature flags at runtime.

This interface lives in relay/kernel/contracts/ because it is a domain
contract — it defines a capability boundary, not an implementation.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from relay.kernel.policies.delivery_policy import DeliveryPolicy
from relay.control_plane.explainability.record import ExplainabilityRecord
from relay.kernel.value_objects.eri import ERIScore
from relay.kernel.value_objects.endpoint_state import EndpointState


class DeliveryStrategy(ABC):
    """
    Abstract contract for a delivery strategy.

    Each implementation decides how to produce a DeliveryPolicy for a given
    endpoint. The Dispatcher calls `evaluate()` and receives a policy +
    explainability record, regardless of which strategy is active.
    """

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """
        Human-readable name for this strategy.
        Used in ExplainabilityRecords and dashboard display.
        Example: 'STRATEGY_A', 'STRATEGY_B', 'STRATEGY_C'
        """
        ...

    @abstractmethod
    def evaluate(
        self,
        endpoint_id: str,
        endpoint_state: EndpointState,
        eri: ERIScore,
        computed_at: datetime,
    ) -> tuple[DeliveryPolicy, ExplainabilityRecord]:
        """
        Evaluate an endpoint and produce a delivery policy.

        Args:
            endpoint_id: The endpoint to evaluate.
            endpoint_state: Current state of the endpoint.
            eri: The most recent ERI score for this endpoint.
            computed_at: Timestamp for determinism. Never use datetime.now().

        Returns:
            A tuple of (DeliveryPolicy, ExplainabilityRecord).
            The policy tells the worker what to do.
            The record explains why.
        """
        ...
