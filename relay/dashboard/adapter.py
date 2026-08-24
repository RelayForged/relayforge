from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _timestamp() -> str:
    """Return a UTC ISO-8601 timestamp for dashboard-generated data."""
    return datetime.now(timezone.utc).isoformat()


def _get_value(obj: Any, *names: str, default: Any = None) -> Any:
    """Safely read an attribute or dictionary key from an object."""
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]

        if hasattr(obj, name):
            return getattr(obj, name)

    return default


def create_eri_point(eri: int | float) -> dict[str, Any]:
    """Convert an ERI value into the dashboard ERI-point contract."""
    return {
        "timestamp": _timestamp(),
        "value": int(eri),
    }


def get_delivery_events() -> list[dict[str, Any]]:
    """
    Return delivery events for the dashboard.

    The Data Plane delivery pipeline is not implemented yet, so the
    dashboard exposes an empty collection instead of inventing data.
    """
    return []


def get_policy_decision(policy: Any | None = None) -> dict[str, Any]:
    """Translate a real DeliveryPolicy into the dashboard contract."""
    if policy is None:
        return {
            "decision": "No policy decision",
            "selected_target": "",
            "reason": "",
        }

    endpoint_state = _get_value(policy, "endpoint_state", default="")

    retry_policy = _get_value(policy, "retry_policy")
    concurrency_policy = _get_value(policy, "concurrency_policy")
    isolation_policy = _get_value(policy, "isolation_policy")

    isolation_action = _get_value(
        isolation_policy,
        "action",
        default="",
    )

    backoff_strategy = _get_value(
        retry_policy,
        "backoff_strategy",
        default="",
    )

    if hasattr(backoff_strategy, "value"):
        backoff_strategy = backoff_strategy.value

    if hasattr(isolation_action, "value"):
        isolation_action = isolation_action.value

    return {
        "decision": str(endpoint_state),
        "selected_target": str(
            _get_value(policy, "endpoint_id", default="")
        ),
        "reason": (
            f"Policy profile for endpoint state {endpoint_state}."
        ),
        "policy_id": str(
            _get_value(policy, "policy_id", default="")
        ),
        "endpoint_id": str(
            _get_value(policy, "endpoint_id", default="")
        ),
        "endpoint_state": str(endpoint_state),
        "eri_score": float(
            _get_value(policy, "eri_score", default=0.0)
        ),
        "computed_at": str(
            _get_value(policy, "computed_at", default="")
        ),
        "max_parallel": int(
            _get_value(concurrency_policy, "max_parallel", default=0)
        ),
        "queue_priority": int(
            _get_value(concurrency_policy, "queue_priority", default=0)
        ),
        "max_retries": int(
            _get_value(retry_policy, "max_retries", default=0)
        ),
        "backoff_strategy": str(backoff_strategy),
        "initial_delay_seconds": float(
            _get_value(
                retry_policy,
                "initial_delay_seconds",
                default=0.0,
            )
        ),
        "max_delay_seconds": float(
            _get_value(
                retry_policy,
                "max_delay_seconds",
                default=0.0,
            )
        ),
        "isolation_action": str(isolation_action),
    }


def get_policy_explanation(
    explanation: Any | None = None,
) -> dict[str, Any]:
    """Translate a real ExplainabilityRecord into the dashboard contract."""
    if explanation is None:
        return {
            "title": "No explanation available",
            "reasons": [],
        }

    triggered_thresholds = _get_value(
        explanation,
        "triggered_thresholds",
        default=[],
    )

    policy_changes = _get_value(
        explanation,
        "policy_changes",
        default=[],
    )

    reasons = []

    reason = _get_value(
        explanation,
        "reason",
        default="",
    )

    if reason:
        reasons.append(str(reason))

    reasons.extend(str(item) for item in triggered_thresholds or [])
    reasons.extend(str(item) for item in policy_changes or [])

    return {
        "title": "Policy evaluation",
        "reasons": reasons,
        "record_id": str(
            _get_value(explanation, "record_id", default="")
        ),
        "policy_id": str(
            _get_value(explanation, "policy_id", default="")
        ),
        "endpoint_id": str(
            _get_value(explanation, "endpoint_id", default="")
        ),
        "state_before": str(
            _get_value(explanation, "state_before", default="")
        ),
        "state_after": str(
            _get_value(explanation, "state_after", default="")
        ),
        "state_changed": bool(
            _get_value(explanation, "state_changed", default=False)
        ),
        "strategy_used": str(
            _get_value(explanation, "strategy_used", default="")
        ),
        "evaluated_at": str(
            _get_value(explanation, "evaluated_at", default="")
        ),
        "window_size": int(
            _get_value(explanation, "window_size", default=0)
        ),
        "triggered_thresholds": [
            str(item) for item in triggered_thresholds or []
        ],
        "policy_changes": [
            str(item) for item in policy_changes or []
        ],
    }


def build_state_update(
    *,
    state: Any,
    eri: int | float,
    eri_history: list[dict[str, Any]] | None = None,
    policy: Any | None = None,
    explanation: Any | None = None,
) -> dict[str, Any]:
    """
    Build the stable dashboard state-update contract.

    Teammate-owned domain objects are translated here instead of being
    passed directly to the React frontend.
    """
    return {
        "type": "state_update",
        "state": str(state),
        "eri": int(eri),
        "eri_history": eri_history or [create_eri_point(eri)],
        "delivery_events": get_delivery_events(),
        "policy_decision": get_policy_decision(policy),
        "policy_explanation": get_policy_explanation(explanation),
    }