"""
Shared-secret authentication for the ingestion API (CWE-306).

`RELAY_API_KEYS` holds comma-separated `owner_id:secret` pairs. The
authenticated owner_id is what `POST /endpoints` records, so a client can
no longer assert ownership of an endpoint it does not own.

Dev (`RELAY_ENV` unset or `dev`) with no keys configured stays open, so
`scripts/api_smoke.sh` and local uvicorn keep working. Outside dev an
unconfigured key set returns 503 rather than silently accepting traffic —
a deploy cannot accidentally expose the ingestion surface.
"""
from __future__ import annotations

import hmac
import logging
import os
from functools import lru_cache
from typing import Optional

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)

DEV_OWNER_ID = "dev-local"
API_KEY_HEADER = "X-API-Key"


@lru_cache(maxsize=1)
def _configured_keys() -> dict[str, str]:
    """Map secret -> owner_id. Cached: env is fixed for the process."""
    raw = os.environ.get("RELAY_API_KEYS", "").strip()
    keys: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        owner_id, separator, secret = entry.partition(":")
        if not separator or not owner_id or not secret:
            raise RuntimeError(
                "RELAY_API_KEYS entries must be formatted 'owner_id:secret'"
            )
        keys[secret] = owner_id
    return keys


def _auth_optional() -> bool:
    return os.environ.get("RELAY_ENV", "dev").lower() == "dev"


async def require_owner(
    x_api_key: Optional[str] = Header(default=None, alias=API_KEY_HEADER),
) -> str:
    """
    Resolve the calling principal. Returns the authenticated `owner_id`.

    Compared with `hmac.compare_digest` so a wrong key cannot be recovered
    by timing the comparison.
    """
    keys = _configured_keys()
    if not keys:
        if not _auth_optional():
            logger.error("RELAY_API_KEYS is unset outside dev; refusing requests")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API authentication is not configured",
            )
        return DEV_OWNER_ID

    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{API_KEY_HEADER} header is required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    for secret, owner_id in keys.items():
        if hmac.compare_digest(x_api_key, secret):
            return owner_id
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
    )
