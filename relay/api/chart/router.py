"""FastAPI router for the Chart Data API.

Provides read-only endpoints consumed by Person C's React dashboard.
"""
import asyncio
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status

from relay.api.chart.schemas import (
    BenchmarkResultsResponse,
    BenchmarkRunRequest,
    BenchmarkRunResponse,
    BenchmarkRunResultResponse,
    ContentionEndpointResult,
    DeadLetterItem,
    DeadLetterListResponse,
    EndpointListResponse,
    EndpointStateResponse,
    EndpointSummary,
    ERIHistoryPoint,
    ERIHistoryResponse,
    ExplainabilityListResponse,
    ExplainabilityResponse,
    MetricComparison,
    ContentionTrace,
    MultiEndpointResponse,
    MultiEndpointStrategyResult,
    MultiEndpointTraceResponse,
    PolicyDecisionResponse,
    ScenarioInfo,
    ScenarioListResponse,
    ScenarioPhaseInfo,
    ScenarioTraceResponse,
    StrategyComparison,
    StrategyTrace,
    TraceStep,
)
from relay.api.chart.stores import BenchmarkRun, benchmark_store, state_store

BENCHMARK_STRATEGIES = ("STRATEGY_A", "STRATEGY_B", "STRATEGY_C")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chart", tags=["chart-data"])


@router.get(
    "/endpoints",
    response_model=EndpointListResponse,
    summary="List all tracked endpoints and their current state",
)
async def list_endpoints() -> EndpointListResponse:
    """
    Return all known endpoints with current state and latest ERI score, plus
    each endpoint's target URL and owner so the dashboard can name it.
    """
    endpoint_ids = state_store.list_endpoints()
    summaries = []
    for ep_id in endpoint_ids:
        state = state_store.get_state(ep_id)
        last_eval = state_store.get_latest_evaluation(ep_id)
        registered = await _registered_endpoint(ep_id)
        summaries.append(
            EndpointSummary(
                endpoint_id=ep_id,
                state=state.value if state else "UNKNOWN",
                eri_score=last_eval.eri_score if last_eval else None,
                last_evaluated_at=state_store.get_last_evaluated_at(ep_id),
                target_url=registered.target_url if registered else None,
                owner_id=registered.owner_id if registered else None,
            )
        )
    return EndpointListResponse(endpoints=summaries)


async def _registered_endpoint(endpoint_id: str):
    """The endpoint's registry row, or None if it is unknown or the lookup fails."""
    try:
        # Imported here so the chart router still loads where no database is configured.
        from relay.infrastructure.persistence.endpoint_repository import EndpointRepository

        return await EndpointRepository().get_by_id(endpoint_id)
    except Exception:
        logger.warning("Endpoint lookup failed for endpoint_id=%s", endpoint_id, exc_info=True)
        return None


@router.get(
    "/endpoints/{endpoint_id}/state",
    response_model=EndpointStateResponse,
    summary="Get current endpoint state",
)
async def get_endpoint_state(endpoint_id: str) -> EndpointStateResponse:
    """Return the current 5-state EndpointState for an endpoint."""
    state = state_store.get_state(endpoint_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint {endpoint_id} has not been evaluated yet.",
        )
    return EndpointStateResponse(
        endpoint_id=endpoint_id,
        state=state.value,
        last_evaluated_at=state_store.get_last_evaluated_at(endpoint_id),
    )


@router.get(
    "/endpoints/{endpoint_id}/eri-history",
    response_model=ERIHistoryResponse,
    summary="Get ERI time-series points for charts",
)
async def get_eri_history(
    endpoint_id: str,
    limit: int = Query(default=50, ge=1, le=200, description="Max history points"),
) -> ERIHistoryResponse:
    """Return chronological ERI time-series data formatted for Recharts LineChart."""
    history = state_store.get_eri_history(endpoint_id, limit=limit)
    points = [
        ERIHistoryPoint(
            timestamp=snapshot.computed_at,
            score=snapshot.score,
            state=snapshot.state,
            sub_scores=snapshot.sub_scores,
        )
        for snapshot in history
    ]
    return ERIHistoryResponse(endpoint_id=endpoint_id, points=points)


@router.get(
    "/endpoints/{endpoint_id}/policy",
    response_model=PolicyDecisionResponse,
    summary="Get latest policy decision for an endpoint",
)
async def get_latest_policy(endpoint_id: str) -> PolicyDecisionResponse:
    """Return the most recent policy evaluation and active delivery policy configuration."""
    eval_record = state_store.get_latest_evaluation(endpoint_id)
    if eval_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No policy evaluations found for endpoint {endpoint_id}",
        )

    policy = state_store.get_policy(eval_record.policy_id)
    retry_dict = policy.retry_policy.model_dump() if policy else {}
    concurrency_dict = policy.concurrency_policy.model_dump() if policy else {}
    isolation_action = (
        policy.isolation_policy.action.value
        if policy and hasattr(policy.isolation_policy.action, "value")
        else (str(policy.isolation_policy.action) if policy else "CONTINUE")
    )

    return PolicyDecisionResponse(
        endpoint_id=endpoint_id,
        policy_id=eval_record.policy_id,
        strategy_used=eval_record.strategy_used,
        is_fallback=eval_record.is_fallback,
        fallback_tier=eval_record.fallback_tier,
        eri_score=eval_record.eri_score,
        state_before=eval_record.state_before,
        state_after=eval_record.state_after,
        state_changed=eval_record.state_changed,
        retry_policy=retry_dict,
        concurrency_policy=concurrency_dict,
        isolation_action=isolation_action,
    )


@router.get(
    "/endpoints/{endpoint_id}/explainability",
    response_model=ExplainabilityListResponse,
    summary="Get recent explainability audit records",
)
async def get_explainability_records(
    endpoint_id: str,
    limit: int = Query(default=20, ge=1, le=100, description="Max records to return"),
) -> ExplainabilityListResponse:
    """Return recent immutable explainability audit records justifying AEDE decisions."""
    records = state_store.get_explainability_records(endpoint_id, limit=limit)
    response_records = []
    for r in records:
        response_records.append(
            ExplainabilityResponse(
                record_id=r.record_id,
                endpoint_id=r.endpoint_id,
                policy_id=r.policy_id,
                state_before=r.state_before,
                state_after=r.state_after,
                state_changed=r.state_changed,
                reason=r.reason,
                triggered_thresholds=r.triggered_thresholds,
                policy_changes=r.policy_changes,
                strategy_used=r.strategy_used,
                evaluated_at=r.evaluated_at.isoformat(),
                metrics=r.metrics.model_dump(),
                thresholds=r.thresholds.model_dump(),
            )
        )
    return ExplainabilityListResponse(endpoint_id=endpoint_id, records=response_records)


@router.get(
    "/benchmark/results",
    response_model=BenchmarkResultsResponse,
    summary="Get comparison benchmark results across Strategy A, B, and C",
)
async def get_benchmark_results() -> BenchmarkResultsResponse:
    """Return collected benchmark results comparing the three strategies."""
    runs = benchmark_store.get_runs()
    return BenchmarkResultsResponse(runs=[_run_response(run) for run in runs])


def _run_response(run: BenchmarkRun) -> BenchmarkRunResponse:
    return BenchmarkRunResponse(
        run_id=run.run_id,
        scenario=run.scenario,
        strategy=run.strategy,
        total_events=run.total_events,
        successful_deliveries=run.successful_deliveries,
        failed_deliveries=run.failed_deliveries,
        avg_retry_count=run.avg_retry_count,
        avg_recovery_time_seconds=run.avg_recovery_time_seconds,
        started_at=run.started_at,
        finished_at=run.finished_at,
        scenario_id=run.scenario_id,
        seed=run.seed,
        delivery_success_rate=run.delivery_success_rate,
        total_wasted_retries=run.total_wasted_retries,
        recovery_time_events=run.recovery_time_events,
        quarantine_count=run.quarantine_count,
        total_delay_seconds=run.total_delay_seconds,
    )


def _execute_benchmark(
    scenario_ids: List[int], strategies: List[str], seed: int
) -> List[BenchmarkRun]:
    # Imported here: the benchmark package is only needed when a run is requested.
    from benchmark.metrics import summarise
    from benchmark.runner import BenchmarkRunner, DeliveryStrategyConfig
    from benchmark.scenarios import get_scenario

    runs: List[BenchmarkRun] = []
    for scenario_id in scenario_ids:
        scenario = get_scenario(scenario_id)
        for strategy in strategies:
            result = BenchmarkRunner(
                DeliveryStrategyConfig(strategy=strategy), random_seed=seed
            ).run(scenario)
            summary = summarise(result)
            runs.append(
                BenchmarkRun(
                    run_id=summary["run_id"],
                    scenario=summary["scenario_name"],
                    strategy=strategy,
                    total_events=summary["total_events"],
                    successful_deliveries=summary["successful_deliveries"],
                    failed_deliveries=summary["failed_deliveries"],
                    avg_retry_count=summary["avg_retry_count"],
                    avg_recovery_time_seconds=float(summary["recovery_time_events"]),
                    started_at=summary["started_at"],
                    finished_at=summary["finished_at"] or summary["started_at"],
                    scenario_id=scenario_id,
                    seed=seed,
                    delivery_success_rate=summary["delivery_success_rate"],
                    total_wasted_retries=summary["total_wasted_retries"],
                    recovery_time_events=summary["recovery_time_events"],
                    quarantine_count=summary["quarantine_count"],
                    total_delay_seconds=summary["total_delay_seconds"],
                )
            )
    return runs


def _metric(strategy_c: float, baseline: float, *, points: bool = False) -> MetricComparison:
    if points:
        change: Optional[float] = round((strategy_c - baseline) * 100.0, 2)
    elif baseline == 0:
        change = None
    else:
        change = round((strategy_c - baseline) / baseline * 100.0, 2)
    return MetricComparison(
        strategy_c=round(strategy_c, 4), baseline=round(baseline, 4), pct_change=change
    )


def _compare(runs: List[BenchmarkRun]) -> Dict[str, StrategyComparison]:
    """
    Strategy C against each baseline, over the scenarios where both ran.
    Every figure is computed from the runs; nothing here is a fixed claim.
    """
    by_strategy: Dict[str, Dict[int, BenchmarkRun]] = {}
    for run in runs:
        by_strategy.setdefault(run.strategy, {})[run.scenario_id] = run

    comparison: Dict[str, StrategyComparison] = {}
    c_runs = by_strategy.get("STRATEGY_C", {})
    for baseline in ("STRATEGY_A", "STRATEGY_B"):
        base_runs = by_strategy.get(baseline, {})
        shared = sorted(set(c_runs) & set(base_runs))
        if not shared:
            continue
        c = [c_runs[i] for i in shared]
        b = [base_runs[i] for i in shared]
        comparison[f"c_vs_{baseline[-1].lower()}"] = StrategyComparison(
            baseline=baseline,
            scenarios=shared,
            wasted_retries=_metric(
                sum(r.total_wasted_retries for r in c),
                sum(r.total_wasted_retries for r in b),
            ),
            total_delay_seconds=_metric(
                sum(r.total_delay_seconds for r in c),
                sum(r.total_delay_seconds for r in b),
            ),
            success_rate=_metric(
                sum(r.delivery_success_rate for r in c) / len(c),
                sum(r.delivery_success_rate for r in b) / len(b),
                points=True,
            ),
        )
    return comparison


@router.post(
    "/benchmark/run",
    response_model=BenchmarkRunResultResponse,
    summary="Run the simulated strategy comparison and store the results for charting",
)
async def run_benchmark(body: BenchmarkRunRequest) -> BenchmarkRunResultResponse:
    """
    Run benchmark scenarios for the requested strategies.

    This is the one non-GET route on the dashboard backend. It is a pure,
    seeded simulation over synthetic telemetry: it reads and writes no
    platform data, and its only effect is the in-memory result list that
    GET /benchmark/results serves. The dashboard remains read-only with
    respect to events, deliveries, endpoints and policies.
    """
    from benchmark.scenarios import ALL_SCENARIOS, EVALUATION_SCENARIO_IDS

    known = {scenario.scenario_id for scenario in ALL_SCENARIOS}
    scenario_ids = body.scenario_ids or list(EVALUATION_SCENARIO_IDS)
    strategies = body.strategies or list(BENCHMARK_STRATEGIES)

    unknown_scenarios = sorted(set(scenario_ids) - known)
    unknown_strategies = sorted(set(strategies) - set(BENCHMARK_STRATEGIES))
    if unknown_scenarios or unknown_strategies:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "unknown_scenario_ids": unknown_scenarios,
                "unknown_strategies": unknown_strategies,
                "known_strategies": list(BENCHMARK_STRATEGIES),
            },
        )

    runs = await asyncio.to_thread(
        _execute_benchmark,
        list(dict.fromkeys(scenario_ids)),
        list(dict.fromkeys(strategies)),
        body.seed,
    )
    for run in runs:
        benchmark_store.replace_run(run)

    return BenchmarkRunResultResponse(
        runs=[_run_response(run) for run in runs],
        comparison=_compare(runs),
    )


@lru_cache(maxsize=1)
def _multi_endpoint_results() -> MultiEndpointResponse:
    # Deterministic (fixed seed), so one computation serves every request.
    from benchmark.multi_endpoint import run_full_multi_endpoint_comparison

    def endpoint(result) -> ContentionEndpointResult:
        return ContentionEndpointResult(
            endpoint_id=result.endpoint_id,
            total_events=result.total_events,
            successful_deliveries=result.successful_deliveries,
            throughput_events_per_sec=result.throughput_events_per_sec,
            avg_queue_wait_ms=result.avg_queue_wait_ms,
            p95_queue_wait_ms=result.p95_queue_wait_ms,
            total_retries=result.total_retries,
        )

    results = run_full_multi_endpoint_comparison()
    return MultiEndpointResponse(
        worker_pool_size=next(iter(results.values())).worker_pool_size,
        strategies=[
            MultiEndpointStrategyResult(
                strategy=strategy,
                quarantine_triggered=result.quarantine_triggered,
                total_sim_time_sec=result.total_sim_time_sec,
                failing=endpoint(result.failing_result),
                healthy=endpoint(result.healthy_result),
            )
            for strategy, result in results.items()
        ],
    )


@router.get(
    "/benchmark/multi-endpoint",
    response_model=MultiEndpointResponse,
    summary="Noisy-neighbor comparison: a failing and a healthy endpoint on one worker pool",
)
async def get_multi_endpoint_comparison() -> MultiEndpointResponse:
    """Simulated contention results per strategy, for the Multi-Endpoint tab."""
    return await asyncio.to_thread(_multi_endpoint_results)


@router.get(
    "/dlq",
    response_model=DeadLetterListResponse,
    summary="List deliveries that need an operator (failed, dead-lettered, or parked)",
)
async def list_dead_letters(
    endpoint_id: Optional[str] = Query(default=None, description="Limit to one endpoint"),
    limit: int = Query(default=50, ge=1, le=500, description="Max items to return"),
) -> DeadLetterListResponse:
    """Read-only view of the dead-letter set. Replay is requested through the API, not here."""
    # Imported here so the chart router still loads where no database is configured.
    from relay.infrastructure.persistence.delivery_repository import DeliveryRepository

    dead_letters = await DeliveryRepository().get_dead_letters(
        now=datetime.now(timezone.utc),
        endpoint_id=endpoint_id,
        limit=limit,
    )
    return DeadLetterListResponse(
        items=[
            DeadLetterItem(
                delivery_id=item.delivery.delivery_id,
                event_id=item.delivery.event_id,
                event_type=item.event_type,
                endpoint_id=item.delivery.endpoint_id,
                reason=item.reason,
                attempt_count=item.attempt_count,
                last_error=item.last_error,
                failed_at=item.failed_at.isoformat(),
            )
            for item in dead_letters
        ]
    )


# --------------------------------------------------------------------------
# Scenario Lab
# --------------------------------------------------------------------------


def _scenario_info(scenario) -> ScenarioInfo:
    from benchmark.scenarios import EVALUATION_SCENARIO_IDS

    return ScenarioInfo(
        scenario_id=scenario.scenario_id,
        name=scenario.name,
        description=scenario.description,
        evaluation_set="held_out" if scenario.scenario_id in EVALUATION_SCENARIO_IDS else "tuning",
        total_events=sum(phase.duration_events for phase in scenario.phases),
        phases=[
            ScenarioPhaseInfo(
                description=phase.description,
                events=phase.duration_events,
                success_rate=phase.success_rate,
                latency_ms=phase.latency_ms,
                timed_out_rate=phase.timed_out_rate,
                http_5xx_rate=phase.http_5xx_rate,
                manual_restore=phase.manual_restore,
            )
            for phase in scenario.phases
        ],
    )


@router.get(
    "/scenarios",
    response_model=ScenarioListResponse,
    summary="All 14 benchmark scenarios, with their fault scripts",
)
async def list_scenarios() -> ScenarioListResponse:
    from benchmark.scenarios import get_all_scenarios

    return ScenarioListResponse(scenarios=[_scenario_info(s) for s in get_all_scenarios()])


def _trace_scenario(scenario_id: int, seed: int) -> ScenarioTraceResponse:
    from benchmark.metrics import summarise
    from benchmark.runner import BenchmarkRunner, DeliveryStrategyConfig
    from benchmark.scenarios import get_scenario

    scenario = get_scenario(scenario_id)
    # Which phase each event belongs to: phases run in order, back to back.
    phase_of = [
        index for index, phase in enumerate(scenario.phases) for _ in range(phase.duration_events)
    ]
    traces = []
    for strategy in BENCHMARK_STRATEGIES:
        result = BenchmarkRunner(DeliveryStrategyConfig(strategy=strategy), random_seed=seed).run(scenario)
        summary = summarise(result)
        traces.append(
            StrategyTrace(
                strategy=strategy,
                steps=[
                    TraceStep(
                        phase=phase_of[min(i, len(phase_of) - 1)],
                        succeeded=attempt.succeeded,
                        eri=round(attempt.eri_score, 2),
                        state=attempt.final_state,
                        retries=attempt.retry_count,
                        delay_seconds=round(attempt.delay_seconds, 2),
                        max_parallel=attempt.max_parallel,
                    )
                    for i, attempt in enumerate(result.attempts)
                ],
                delivery_success_rate=summary["delivery_success_rate"],
                total_wasted_retries=summary["total_wasted_retries"],
                total_delay_seconds=summary["total_delay_seconds"],
                quarantine_count=summary["quarantine_count"],
            )
        )
    return ScenarioTraceResponse(scenario=_scenario_info(scenario), seed=seed, strategies=traces)


@router.get(
    "/scenarios/{scenario_id}/trace",
    response_model=ScenarioTraceResponse,
    summary="Every event of one scenario under Strategies A, B and C, for playback",
)
async def trace_scenario(
    scenario_id: int,
    seed: int = Query(default=42, description="Benchmark seed; 42 matches the reported results"),
) -> ScenarioTraceResponse:
    """
    The same deterministic simulation as POST /benchmark/run, but returning
    each event instead of only totals, so the dashboard can replay it. Read
    only: nothing is stored.
    """
    from benchmark.scenarios import get_all_scenarios

    if scenario_id not in {s.scenario_id for s in get_all_scenarios()}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown scenario {scenario_id}")
    return await asyncio.to_thread(_trace_scenario, scenario_id, seed)


@lru_cache(maxsize=1)
def _multi_endpoint_trace() -> MultiEndpointTraceResponse:
    from benchmark.multi_endpoint import run_full_multi_endpoint_comparison

    results = run_full_multi_endpoint_comparison()
    first = next(iter(results.values()))
    return MultiEndpointTraceResponse(
        worker_pool_size=first.worker_pool_size,
        events_per_endpoint=first.healthy_result.total_events,
        arrival_interval_ms=first.arrival_interval_ms,
        # The simulation's default outage start for the failing endpoint.
        failing_from_event=40,
        strategies=[
            ContentionTrace(
                strategy=strategy,
                quarantined_from_event=result.quarantined_from_event,
                total_sim_time_sec=result.total_sim_time_sec,
                jobs=[
                    [
                        0 if job.endpoint_id.endswith("failing") else 1,
                        job.event_idx,
                        round(job.queue_enter_ms, 1),
                        round(job.dispatch_start_ms, 1),
                        round(job.completion_ms, 1),
                        job.worker,
                        1 if job.succeeded else 0,
                        job.retries_scheduled,
                    ]
                    for job in result.timeline
                ],
            )
            for strategy, result in results.items()
        ],
    )


@router.get(
    "/benchmark/multi-endpoint/trace",
    response_model=MultiEndpointTraceResponse,
    summary="The noisy-neighbour simulation job by job, for playback",
)
async def get_multi_endpoint_trace() -> MultiEndpointTraceResponse:
    """Same deterministic run as /benchmark/multi-endpoint, with every job's timing."""
    return await asyncio.to_thread(_multi_endpoint_trace)
