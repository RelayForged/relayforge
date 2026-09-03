"""
Database connection configuration for the persistence layer.

The URL comes from the environment. The docker-compose defaults are
returned *only* when `RELAY_ENV` is unset or `dev`, so a deployment that
forgets DATABASE_URL / ASYNC_DATABASE_URL fails loudly instead of quietly
connecting with the credentials published in docker-compose.yml.
"""
import os

DEV_DATABASE_URL = "postgresql+psycopg://relay:relaypassword@localhost:5432/relaydb"
DEV_ASYNC_DATABASE_URL = "postgresql+asyncpg://relay:relaypassword@localhost:5432/relaydb"

# Retained for backwards compatibility with Week 1-2 imports.
DEFAULT_DATABASE_URL = DEV_DATABASE_URL
DEFAULT_ASYNC_DATABASE_URL = DEV_ASYNC_DATABASE_URL


def _is_dev() -> bool:
    return os.environ.get("RELAY_ENV", "dev").lower() == "dev"


def _resolve(variable: str, dev_default: str) -> str:
    url = os.environ.get(variable)
    if url:
        return url
    if not _is_dev():
        raise RuntimeError(
            f"{variable} must be set when RELAY_ENV is not 'dev'; "
            "refusing to fall back to the docker-compose dev credentials"
        )
    return dev_default


def get_sync_database_url() -> str:
    """Sync driver (psycopg) URL. Used by Alembic migrations."""
    return _resolve("DATABASE_URL", DEV_DATABASE_URL)


def get_async_database_url() -> str:
    """Async driver (asyncpg) URL. Used by the application/repositories at runtime."""
    return _resolve("ASYNC_DATABASE_URL", DEV_ASYNC_DATABASE_URL)
