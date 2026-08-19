"""
Shared async SQLAlchemy engine factory for all repositories.

One AsyncEngine per process (SQLAlchemy pools connections internally).
Repositories accept an engine via constructor injection rather than
importing a global singleton directly, so tests can supply an isolated
engine without monkeypatching.
"""
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from relay.infrastructure.persistence.config import get_async_database_url

_engine: Optional[AsyncEngine] = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_async_database_url(), pool_pre_ping=True)
    return _engine
