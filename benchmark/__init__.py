"""Benchmark framework for evaluating Strategy A vs B vs C across 10 failure scenarios."""
from benchmark.metrics import (
    AttemptRecord,
    BenchmarkResult,
    BenchmarkRunSummary,
    BenchmarkStore,
    benchmark_store,
    compute_avg_retry_count,
    compute_total_wasted_retries,
    compute_delivery_success_rate,
    compute_quarantine_count,
    compute_recovery_time_events,
    compute_state_transition_count,
    summarise,
)
from benchmark.runner import (
    BenchmarkRunner,
    DeliveryStrategyConfig,
    SyntheticTelemetryGenerator,
)
from benchmark.scenarios import (
    ALL_SCENARIOS,
    Scenario,
    ScenarioPhase,
    get_all_scenarios,
    get_scenario,
)

__all__ = [
    "BenchmarkRunner",
    "DeliveryStrategyConfig",
    "SyntheticTelemetryGenerator",
    "Scenario",
    "ScenarioPhase",
    "ALL_SCENARIOS",
    "get_all_scenarios",
    "get_scenario",
    "BenchmarkResult",
    "BenchmarkRunSummary",
    "BenchmarkStore",
    "benchmark_store",
    "AttemptRecord",
    "compute_delivery_success_rate",
    "compute_avg_retry_count",
    "compute_total_wasted_retries",
    "compute_recovery_time_events",
    "compute_state_transition_count",
    "compute_quarantine_count",
    "summarise",
]
