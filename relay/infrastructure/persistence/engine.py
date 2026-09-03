"""
Shared async SQLAlchemy engine factory for all repositories.

One AsyncEngine per process (SQLAlchemy pools connections internally).
Repositories accept an engine via constructor injection rather than
importing a global singleton directly, so tests can supply an isolated
engine without monkeypatching.
"""
import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from relay.infrastructure.persistence.config import get_async_database_url

# SQLAlchemy's defaults (pool_size=5, max_overflow=10) cap the Dispatcher
# at 15 concurrent connections. The Dispatcher runs fan-out and scheduled
# retries concurrently and every repository call checks a connection out,
# so the pool is sized against DISPATCHER_MAX_CONCURRENT_MESSAGES rather
# than left at the library default.
DEFAULT_POOL_SIZE = 20
DEFAULT_MAX_OVERFLOW = 20
DEFAULT_POOL_TIMEOUT_SECONDS = 30.0

_engine: Optional[AsyncEngine] = None


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_async_database_url(),
            pool_pre_ping=True,
            pool_size=_int_env("DB_POOL_SIZE", DEFAULT_POOL_SIZE),
            max_overflow=_int_env("DB_MAX_OVERFLOW", DEFAULT_MAX_OVERFLOW),
            pool_timeout=DEFAULT_POOL_TIMEOUT_SECONDS,
            pool_recycle=1800,
        )
    return _engine
