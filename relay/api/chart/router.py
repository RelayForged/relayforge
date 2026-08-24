"""FastAPI router for the Chart Data API.

Provides read-only endpoints consumed by Person C's React dashboard.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status

from relay.api.chart.schemas import (
    BenchmarkResultsResponse,
    BenchmarkRunResponse,
    EndpointListResponse,
    EndpointStateResponse,
    EndpointSummary,
    ERIHistoryPoint,
    ERIHistoryResponse,
    ExplainabilityListResponse,
    ExplainabilityResponse,
    PolicyDecisionResponse,
)
from relay.api.chart.stores import benchmark_store, state_store

router = APIRouter(prefix="/api/chart", tags=["chart-data"])


@router.get(
    "/endpoints",
    response_model=EndpointListResponse,
    summary="List all tracked endpoints and their current state",
)
async def list_endpoints() -> EndpointListResponse:
    """Return all known endpoints with current state and latest ERI score."""
    endpoint_ids = state_store.list_endpoints()
    summaries = []
    for ep_id in endpoint_ids:
        state = state_store.get_state(ep_id)
        last_eval = state_store.get_latest_evaluation(ep_id)
        summaries.append(
            EndpointSummary(
                endpoint_id=ep_id,
                state=state.value if state else "UNKNOWN",
                eri_score=last_eval.eri_score if last_eval else None,
                last_evaluated_at=state_store.get_last_evaluated_at(ep_id),
            )
        )
    return EndpointListResponse(endpoints=summaries)


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
    return BenchmarkResultsResponse(
        runs=[
            BenchmarkRunResponse(
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
            )
            for run in runs
        ]
    )
