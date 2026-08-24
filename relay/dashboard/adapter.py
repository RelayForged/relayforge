from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

def _timestamp() -> str:
    """Return a UTC ISO-8601 timestamp for dashboard data."""
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

    The delivery pipeline is not implemented yet, so the dashboard
    deliberately exposes an empty collection rather than inventing
    delivery data.
    """
    return []


def get_policy_decision(policy: Any | None = None) -> dict[str, Any]:
    """Convert an existing policy result into the dashboard contract."""
    if policy is None:
        return {
            "decision": "No policy decision",
            "selected_target": "",
            "reason": "",
        }

    decision = _get_value(
        policy,
        "decision",
        "action",
        "policy_decision",
        default="",
    )

    selected_target = _get_value(
        policy,
        "selected_target",
        "target",
        "endpoint_id",
        default="",
    )

    reason = _get_value(
        policy,
        "reason",
        default="",
    )

    return {
        "decision": str(decision),
        "selected_target": str(selected_target),
        "reason": str(reason),
    }


def get_policy_explanation(explanation: Any | None = None) -> dict[str, Any]:
    """Convert an existing explainability result into the dashboard contract."""
    if explanation is None:
        return {
            "title": "No explanation available",
            "reasons": [],
        }

    title = _get_value(
        explanation,
        "title",
        "reason",
        default="",
    )

    reasons = _get_value(
        explanation,
        "reasons",
        "triggered_thresholds",
        "policy_changes",
        default=[],
    )

    if reasons is None:
        reasons = []

    if not isinstance(reasons, list):
        reasons = [str(reasons)]

    return {
        "title": str(title),
        "reasons": [str(reason) for reason in reasons],
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

    This is the integration boundary between teammate implementations
    and the dashboard. Teammate-specific objects should be translated
    here instead of being passed directly to the React frontend.
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