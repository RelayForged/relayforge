"""
Database connection configuration for the persistence layer.

The URL is read from the DATABASE_URL environment variable so credentials
are never hardcoded. The default matches the local docker-compose.yml
Postgres service, so `docker compose up -d` + no env var "just works" in dev.
"""
import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://relay:relaypassword@localhost:5432/relaydb"
DEFAULT_ASYNC_DATABASE_URL = "postgresql+asyncpg://relay:relaypassword@localhost:5432/relaydb"


def get_sync_database_url() -> str:
    """Sync driver (psycopg) URL. Used by Alembic migrations."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_async_database_url() -> str:
    """Async driver (asyncpg) URL. Used by the application/repositories at runtime."""
    return os.environ.get("ASYNC_DATABASE_URL", DEFAULT_ASYNC_DATABASE_URL)
