# Decision: Project Bootstrap (`pyproject.toml`)

- **Context:** Step 1 required setting up `pyproject.toml` with pydantic, sqlalchemy, alembic, an async Postgres driver (`asyncpg`/`psycopg`), aiokafka, fastapi, and uvicorn, and making `relay` installable via `pip install -e .`.
- **Decision:**
  - Included **both** `asyncpg` (for SQLAlchemy's async engine) and `psycopg[binary]` (sync driver, needed by Alembic's default sync migration runner) rather than picking a single driver.
  - Used `setuptools` as the build backend with `[tool.setuptools.packages.find] include = ["relay*"]` to scope package discovery to the `relay` package only (avoids accidentally packaging `scripts/`, `docs/`, etc.).
  - Set `requires-python = ">=3.11"` per ADR 0006.
- **Why:**
  - Alembic migrations typically run synchronously even when the app uses an async engine at runtime — keeping `psycopg` avoids forcing an async-only Alembic `env.py` (extra complexity not justified in Week 1).
  - `asyncpg` remains the runtime driver for the FastAPI app / repositories per ADR 0007's async requirement.
  - Explicit package inclusion prevents `setuptools` auto-discovery from picking up top-level non-package directories.

