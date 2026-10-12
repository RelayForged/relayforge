"""
ExplainabilityRecord PostgreSQL Repository Abstraction.

Persists ExplainabilityRecords to PostgreSQL for auditability and Dashboard retrieval.
Implements in-memory fallback storage when database connection is not active.
"""

from typing import Optional, Protocol
from relay.control_plane.explainability.record import ExplainabilityRecord


class ExplainabilityRepository(Protocol):
    """Protocol contract for ExplainabilityRecord persistence."""

    def save(self, record: ExplainabilityRecord) -> None:
        """Persist an explainability record."""
        ...

    def get_by_policy_id(self, policy_id: str) -> Optional[ExplainabilityRecord]:
        """Fetch explainability record by policy_id."""
        ...

    def get_latest_by_endpoint(self, endpoint_id: str) -> Optional[ExplainabilityRecord]:
        """Fetch latest explainability record for an endpoint."""
        ...


class InMemoryExplainabilityRepository:
    """
    In-memory implementation of ExplainabilityRepository for unit tests.
    """

    def __init__(self):
        self._by_policy_id: dict[str, ExplainabilityRecord] = {}
        self._by_endpoint: dict[str, list[ExplainabilityRecord]] = {}

    def save(self, record: ExplainabilityRecord) -> None:
        self._by_policy_id[record.policy_id] = record
        if record.endpoint_id not in self._by_endpoint:
            self._by_endpoint[record.endpoint_id] = []
        self._by_endpoint[record.endpoint_id].append(record)

    def get_by_policy_id(self, policy_id: str) -> Optional[ExplainabilityRecord]:
        return self._by_policy_id.get(policy_id)

    def get_latest_by_endpoint(self, endpoint_id: str) -> Optional[ExplainabilityRecord]:
        records = self._by_endpoint.get(endpoint_id, [])
        if not records:
            return None
        return records[-1]
