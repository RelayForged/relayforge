"""In-memory data stores for the Chart Data API.

These are singleton state containers that accumulate operational state as the
Control Plane pipeline runs. The Chart Data API reads from them (strictly read-only).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from relay.control_plane.explainability.record import ExplainabilityRecord
from relay.kernel.entities.policy_evaluation import PolicyEvaluation
from relay.kernel.policies.delivery_policy import DeliveryPolicy
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore


@dataclass(frozen=True)
class ERISnapshot:
    """An immutable time-series data point of an ERI evaluation."""
    score: float
    sub_scores: Dict[str, float]
    computed_at: str
    state: str


@dataclass(frozen=True)
class BenchmarkRun:
    """An immutable record of an executed benchmark scenario run."""
    run_id: str
    scenario: str
    strategy: str  # STRATEGY_A, STRATEGY_B, STRATEGY_C
    total_events: int
    successful_deliveries: int
    failed_deliveries: int
    avg_retry_count: float
    avg_recovery_time_seconds: float
    started_at: str
    finished_at: str


class EndpointStateStore:
    """In-memory storage for endpoint state, ERI history, and policy evaluations.
    
    Capped at maximum thresholds to prevent unbounded memory growth.
    """

    def __init__(self, max_eri_points: int = 200, max_evaluations: int = 100) -> None:
        self.max_eri_points = max_eri_points
        self.max_evaluations = max_evaluations
        self._states: Dict[str, EndpointState] = {}
        self._last_evaluated: Dict[str, str] = {}
        self._eri_history: Dict[str, List[ERISnapshot]] = {}
        self._policy_evaluations: Dict[str, List[PolicyEvaluation]] = {}
        self._policies: Dict[str, DeliveryPolicy] = {}
        self._explainability_records: Dict[str, List[ExplainabilityRecord]] = {}

    def record_evaluation(
        self,
        endpoint_id: str,
        state: EndpointState,
        eri_score: ERIScore,
        policy_evaluation: PolicyEvaluation,
        delivery_policy: DeliveryPolicy,
        explainability_record: Optional[ExplainabilityRecord] = None,
    ) -> None:
        """Record an evaluation cycle from the Control Plane loop."""
        self._states[endpoint_id] = state
        now_iso = eri_score.computed_at.isoformat()
        self._last_evaluated[endpoint_id] = now_iso

        # Record ERI snapshot
        sub_scores = {
            "success_rate": eri_score.success_rate_score,
            "latency": eri_score.latency_score,
            "consecutive_failures": eri_score.consecutive_failure_score,
            "timeout_rate": eri_score.timeout_score,
            "http_5xx_rate": eri_score.http_5xx_score,
            "queue_depth": eri_score.queue_depth_score,
            "recovery_trend": eri_score.recovery_trend_score,
        }
        snapshot = ERISnapshot(
            score=eri_score.score,
            sub_scores=sub_scores,
            computed_at=now_iso,
            state=state.value,
        )
        if endpoint_id not in self._eri_history:
            self._eri_history[endpoint_id] = []
        self._eri_history[endpoint_id].append(snapshot)
        if len(self._eri_history[endpoint_id]) > self.max_eri_points:
            self._eri_history[endpoint_id] = self._eri_history[endpoint_id][-self.max_eri_points:]

        # Record PolicyEvaluation
        if endpoint_id not in self._policy_evaluations:
            self._policy_evaluations[endpoint_id] = []
        self._policy_evaluations[endpoint_id].append(policy_evaluation)
        if len(self._policy_evaluations[endpoint_id]) > self.max_evaluations:
            self._policy_evaluations[endpoint_id] = self._policy_evaluations[endpoint_id][-self.max_evaluations:]

        # Record DeliveryPolicy
        self._policies[delivery_policy.policy_id] = delivery_policy

        # Record ExplainabilityRecord
        if explainability_record:
            if endpoint_id not in self._explainability_records:
                self._explainability_records[endpoint_id] = []
            self._explainability_records[endpoint_id].append(explainability_record)
            if len(self._explainability_records[endpoint_id]) > self.max_evaluations:
                self._explainability_records[endpoint_id] = self._explainability_records[endpoint_id][-self.max_evaluations:]

    def get_state(self, endpoint_id: str) -> Optional[EndpointState]:
        return self._states.get(endpoint_id)

    def get_last_evaluated_at(self, endpoint_id: str) -> Optional[str]:
        return self._last_evaluated.get(endpoint_id)

    def get_eri_history(self, endpoint_id: str, limit: int = 50) -> List[ERISnapshot]:
        history = self._eri_history.get(endpoint_id, [])
        return history[-limit:]

    def get_latest_evaluation(self, endpoint_id: str) -> Optional[PolicyEvaluation]:
        evals = self._policy_evaluations.get(endpoint_id, [])
        return evals[-1] if evals else None

    def get_policy(self, policy_id: str) -> Optional[DeliveryPolicy]:
        return self._policies.get(policy_id)

    def get_explainability_records(self, endpoint_id: str, limit: int = 20) -> List[ExplainabilityRecord]:
        records = self._explainability_records.get(endpoint_id, [])
        return records[-limit:]

    def list_endpoints(self) -> List[str]:
        return list(self._states.keys())

    def get_latest_endpoint_id(self) -> Optional[str]:
        """The endpoint with the most recent evaluation, or None if nothing has been evaluated."""
        if not self._last_evaluated:
            return None
        return max(self._last_evaluated, key=self._last_evaluated.__getitem__)

    def clear(self) -> None:
        """Clear all stored state (useful for tests)."""
        self._states.clear()
        self._last_evaluated.clear()
        self._eri_history.clear()
        self._policy_evaluations.clear()
        self._policies.clear()
        self._explainability_records.clear()


class BenchmarkResultStore:
    """In-memory storage for benchmark comparison runs."""

    def __init__(self) -> None:
        self._runs: List[BenchmarkRun] = []

    def add_run(self, run: BenchmarkRun) -> None:
        self._runs.append(run)

    def get_runs(self) -> List[BenchmarkRun]:
        return list(self._runs)

    def get_latest_run(self) -> Optional[BenchmarkRun]:
        return self._runs[-1] if self._runs else None

    def clear(self) -> None:
        self._runs.clear()


# Global singletons
state_store = EndpointStateStore()
benchmark_store = BenchmarkResultStore()
