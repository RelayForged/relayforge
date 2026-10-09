"""Metric computation and evaluation records for benchmark experiments.

Computes delivery success rate, wasted retry count, state stability, and MTTR.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass(frozen=True)
class AttemptRecord:
    """Record of a single simulated event attempt within a benchmark run."""
    succeeded: bool
    retry_count: int
    latency_ms: float
    timed_out: bool
    final_state: str
    eri_score: float
    delay_seconds: float = 0.0
    max_parallel: int = 1


@dataclass
class BenchmarkResult:
    """Aggregated execution result of a single benchmark experiment run."""
    scenario_id: int
    scenario_name: str
    strategy: str  # STRATEGY_A, STRATEGY_B, STRATEGY_C
    attempts: List[AttemptRecord] = field(default_factory=list)
    state_transitions: List[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    run_id: str = field(default_factory=lambda: str(uuid4()))


def compute_delivery_success_rate(result: BenchmarkResult) -> float:
    """Calculate the fraction of delivery attempts that succeeded."""
    if not result.attempts:
        return 0.0
    successes = sum(1 for a in result.attempts if a.succeeded)
    return round(successes / len(result.attempts), 4)


def compute_avg_retry_count(result: BenchmarkResult) -> float:
    """Calculate average retry attempts incurred per event."""
    if not result.attempts:
        return 0.0
    total_retries = sum(a.retry_count for a in result.attempts)
    return round(total_retries / len(result.attempts), 4)


def compute_total_wasted_retries(result: BenchmarkResult) -> int:
    """Calculate total wasted retry attempts incurred across all events."""
    if not result.attempts:
        return 0
    return sum(a.retry_count for a in result.attempts)


def compute_recovery_time_events(result: BenchmarkResult) -> int:
    """Calculate the event count from the first degradation to returning to AVAILABLE."""
    if not result.attempts:
        return 0

    degradation_start_idx: Optional[int] = None
    recovered_idx: Optional[int] = None

    for idx, attempt in enumerate(result.attempts):
        if attempt.final_state != "AVAILABLE" and degradation_start_idx is None:
            degradation_start_idx = idx
        elif degradation_start_idx is not None and attempt.final_state == "AVAILABLE":
            recovered_idx = idx
            break

    if degradation_start_idx is None:
        return 0  # Never degraded
    if recovered_idx is None:
        return len(result.attempts) - degradation_start_idx  # Degraded and never recovered

    return recovered_idx - degradation_start_idx


def compute_state_transition_count(result: BenchmarkResult) -> int:
    """Calculate total state machine transitions (lower indicates higher stability/less flapping)."""
    if len(result.state_transitions) <= 1:
        return 0
    return len(result.state_transitions) - 1


def compute_quarantine_count(result: BenchmarkResult) -> int:
    """Count how many times the endpoint transitioned into QUARANTINED."""
    count = 0
    for i in range(1, len(result.state_transitions)):
        if result.state_transitions[i] == "QUARANTINED" and result.state_transitions[i - 1] != "QUARANTINED":
            count += 1
    return count


def compute_total_retry_delay_seconds(result: BenchmarkResult) -> float:
    """Calculate cumulative retry delay seconds across all attempts."""
    if not result.attempts:
        return 0.0
    return round(sum(a.delay_seconds for a in result.attempts), 2)


def compute_avg_retry_delay_seconds(result: BenchmarkResult) -> float:
    """Calculate average retry delay seconds per attempt."""
    if not result.attempts:
        return 0.0
    return round(sum(a.delay_seconds for a in result.attempts) / len(result.attempts), 2)


def compute_peak_concurrency(result: BenchmarkResult) -> int:
    """Calculate peak concurrency (max_parallel) utilized across attempts."""
    if not result.attempts:
        return 1
    return max((a.max_parallel for a in result.attempts), default=1)


@dataclass(frozen=True)
class BenchmarkRunSummary:
    """Immutable summary record of an executed benchmark run."""
    run_id: str
    scenario: str
    strategy: str
    total_events: int
    successful_deliveries: int
    failed_deliveries: int
    avg_retry_count: float
    avg_recovery_time_seconds: float
    started_at: str
    finished_at: str


class BenchmarkStore:
    """In-memory store for benchmark run summaries."""

    def __init__(self) -> None:
        self._runs: List[BenchmarkRunSummary] = []

    def add_run(self, run: BenchmarkRunSummary) -> None:
        self._runs.append(run)

    def get_runs(self) -> List[BenchmarkRunSummary]:
        return list(self._runs)

    def get_latest_run(self) -> Optional[BenchmarkRunSummary]:
        return self._runs[-1] if self._runs else None

    def clear(self) -> None:
        self._runs.clear()


benchmark_store = BenchmarkStore()


def summarise(result: BenchmarkResult) -> Dict[str, Any]:
    """Summarise benchmark results into a flat dictionary for reporting and persistence."""
    total_events = len(result.attempts)
    successful = sum(1 for a in result.attempts if a.succeeded)
    failed = total_events - successful

    return {
        "run_id": result.run_id,
        "scenario_id": result.scenario_id,
        "scenario_name": result.scenario_name,
        "strategy": result.strategy,
        "total_events": total_events,
        "successful_deliveries": successful,
        "failed_deliveries": failed,
        "delivery_success_rate": compute_delivery_success_rate(result),
        "total_wasted_retries": compute_total_wasted_retries(result),
        "avg_retry_count": compute_avg_retry_count(result),
        "recovery_time_events": compute_recovery_time_events(result),
        "state_transitions": compute_state_transition_count(result),
        "quarantine_count": compute_quarantine_count(result),
        "total_delay_seconds": compute_total_retry_delay_seconds(result),
        "avg_delay_seconds": compute_avg_retry_delay_seconds(result),
        "peak_concurrency": compute_peak_concurrency(result),
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
    }

