"""SDK exception hierarchy.

All exceptions raised by the Relay SDK inherit from RelayError.
"""
from typing import Any, Dict, Optional


class RelayError(Exception):
    """Base exception for all SDK errors."""
    pass


class RelayAPIError(RelayError):
    """Raised when the Relay API returns a non-success HTTP status code."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        response_body: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.response_body = response_body
        super().__init__(f"HTTP {status_code}: {detail}")


class RelayConnectionError(RelayError):
    """Raised when the SDK cannot establish a connection to the Relay API."""
    pass
