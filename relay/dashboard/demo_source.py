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

    def __init__(
        self,
        endpoint_id: str = "dashboard-demo-endpoint",
        failure_rate: float = 0.8,
        latency_ms: float = 150.0,
        failure_mode: str = "mixed",
    ) -> None:
        if not 0.0 <= failure_rate <= 1.0:
            raise ValueError("failure_rate must be between 0.0 and 1.0")

        if latency_ms <= 0:
            raise ValueError("latency_ms must be greater than 0")

        if failure_mode not in {"mixed", "http_500", "timeout"}:
            raise ValueError(
                "failure_mode must be one of: mixed, http_500, timeout"
            )

        self.endpoint_id = endpoint_id
        self.failure_rate = failure_rate
        self.latency_ms = latency_ms
        self.failure_mode = failure_mode

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

    def configure(
        self,
        *,
        failure_rate: float | None = None,
        latency_ms: float | None = None,
        failure_mode: str | None = None,
    ) -> None:
        """
        Update dashboard demo fault-injection settings.

        This is intentionally dashboard-owned configuration. It does not
        modify the ERI calculation or Strategy C behavior.
        """
        if failure_rate is not None:
            if not 0.0 <= failure_rate <= 1.0:
                raise ValueError(
                    "failure_rate must be between 0.0 and 1.0"
                )
            self.failure_rate = failure_rate

        if latency_ms is not None:
            if latency_ms <= 0:
                raise ValueError(
                    "latency_ms must be greater than 0"
                )
            self.latency_ms = latency_ms

        if failure_mode is not None:
            if failure_mode not in {
                "mixed",
                "http_500",
                "timeout",
            }:
                raise ValueError(
                    "failure_mode must be one of: "
                    "mixed, http_500, timeout"
                )

            self.failure_mode = failure_mode

    def _make_attempt(self) -> Telemetry:
        self.attempt_number += 1

        # A zero failure rate means the demo remains healthy indefinitely.
        # This is important for testing recovery/stable operation.
        if self.failure_rate == 0.0:
            succeeded = True
            timed_out = False
            latency_ms = self.latency_ms
            http_status = 200

        # Phase 1: healthy startup.
        # Give Strategy C a healthy baseline before fault injection begins.
        elif self.attempt_number <= 10:
            succeeded = True
            timed_out = False
            latency_ms = self.latency_ms
            http_status = 200

        # Phase 2: configurable degradation.
        elif self.attempt_number <= 30:
            failure = (
                (self.attempt_number % 10) / 10
            ) < self.failure_rate

            if not failure:
                succeeded = True
                timed_out = False
                latency_ms = self.latency_ms
                http_status = 200

            elif self.failure_mode == "timeout":
                succeeded = False
                timed_out = True
                latency_ms = max(self.latency_ms, 3000.0)
                http_status = None

            elif self.failure_mode == "http_500":
                succeeded = False
                timed_out = False
                latency_ms = max(self.latency_ms, 1800.0)
                http_status = 500

            else:
                # Mixed mode alternates between timeout and HTTP 500.
                if self.attempt_number % 2 == 0:
                    succeeded = False
                    timed_out = True
                    latency_ms = max(self.latency_ms, 3000.0)
                    http_status = None
                else:
                    succeeded = False
                    timed_out = False
                    latency_ms = max(self.latency_ms, 1800.0)
                    http_status = 500

        # Phase 3: severe outage.
        # Keep this phase so the default dashboard demo can demonstrate
        # the full Strategy C state progression.
        else:
            succeeded = False
            timed_out = True
            latency_ms = max(self.latency_ms, 3000.0)
            http_status = None

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