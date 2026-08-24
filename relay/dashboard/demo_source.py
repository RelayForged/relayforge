from __future__ import annotations

from datetime import datetime, timezone

from relay.control_plane.aede.strategies.aede_strategy import StrategyC
from relay.control_plane.eri.calculator import compute_eri
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIWeights
from relay.kernel.value_objects.telemetry import Telemetry


class DashboardDemoSource:
    """
    Dashboard-owned telemetry source used until the real Data Plane
    telemetry pipeline is available.

    IMPORTANT:
    This does not invent an ERI value directly.

    It generates Telemetry observations and feeds them through the
    real Control Plane:

        Telemetry
            -> compute_eri()
            -> StrategyC
            -> DeliveryPolicy + ExplainabilityRecord
    """

    def __init__(self, endpoint_id: str = "dashboard-demo-endpoint") -> None:
        self.endpoint_id = endpoint_id
        self.state = EndpointState.AVAILABLE
        self.attempt_number = 0

        self.attempts: list[Telemetry] = []

        self.weights = ERIWeights(
            success_rate_weight=0.35,
            latency_weight=0.20,
            consecutive_failures_weight=0.20,
            timeout_weight=0.10,
            http_5xx_weight=0.05,
            queue_depth_weight=0.05,
            recovery_trend_weight=0.05,
        )

        self.strategy = StrategyC()

    def _make_attempt(self) -> Telemetry:
        self.attempt_number += 1

        # Deterministic demo pattern:
        # every 4th attempt fails. This makes the ERI visibly change
        # during a dashboard demonstration without directly inventing
        # an ERI value.
        failure = self.attempt_number % 4 == 0

        if failure:
            # Alternate between a slow 5xx and a timeout so that
            # different ERI dimensions are exercised.
            if self.attempt_number % 8 == 0:
                timed_out = True
                latency_ms = 3000.0
                http_status = None
            else:
                timed_out = False
                latency_ms = 1800.0
                http_status = 500

            succeeded = False

        else:
            timed_out = False
            latency_ms = 150.0
            http_status = 200
            succeeded = True

        return Telemetry(
            attempt_id=f"dashboard-demo-attempt-{self.attempt_number}",
            endpoint_id=self.endpoint_id,
            succeeded=succeeded,
            http_status=http_status,
            latency_ms=latency_ms,
            timed_out=timed_out,
            observed_at=datetime.now(timezone.utc),
        )

    def next_evaluation(self) -> tuple[int, EndpointState, object, object]:
        """
        Generate one telemetry observation and evaluate the real
        Control Plane.

        Returns:
            eri score,
            resulting endpoint state,
            DeliveryPolicy,
            ExplainabilityRecord
        """
        attempt = self._make_attempt()

        self.attempts.append(attempt)

        # Match the platform's configured attempt-window concept.
        self.attempts = self.attempts[-50:]

        computed_at = datetime.now(timezone.utc)

        eri = compute_eri(
            attempts=self.attempts,
            queue_depth=0,
            weights=self.weights,
            baseline_latency_ms=500.0,
            max_latency_ms=10000.0,
            max_consecutive_failures=10,
            max_queue_depth=500,
            computed_at=computed_at,
        )

        policy, explanation = self.strategy.evaluate(
            endpoint_id=self.endpoint_id,
            endpoint_state=self.state,
            eri=eri,
            computed_at=computed_at,
        )

        self.state = EndpointState(policy.endpoint_state)

        return int(round(eri.score)), self.state, policy, explanation