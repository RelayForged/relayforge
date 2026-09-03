# Decision: Environment gating, declared dependencies, and package markers

- **Context:** Cleanup items from the audit that cut across the package rather than belonging to one component, plus the environment-variable inventory the fixes introduced.

- **Decision 1 — Dev database credentials only when `RELAY_ENV` is unset or `dev`:** `get_sync_database_url()` / `get_async_database_url()` return the compose defaults in dev and raise `RuntimeError` otherwise. `DEFAULT_DATABASE_URL` / `DEFAULT_ASYNC_DATABASE_URL` are kept as aliases for Week 1–2 imports.
  - **Why:** The module docstring claimed credentials "are never hardcoded", but the literals were the *fallback* when the env var was unset — so a deployment that forgot `ASYNC_DATABASE_URL` silently connected with the credentials committed in `docker-compose.yml` instead of failing. This is the guard that lets `compose-dev-hardening.md` keep friendly dev defaults. `RELAY_ENV` is also what gates API authentication (ADR 0018), so one variable flips both.

- **Decision 2 — Declare `pyyaml`:** added to `[project].dependencies`.
  - **Why:** `relay/infrastructure/config_loader.py` has imported `yaml` since Week 1–2 and it was never declared. A clean `pip install -e .` produced a module that raises `ImportError` on import. It works today only because pyyaml arrives transitively.

- **Decision 3 — No packaging change: every package already has an `__init__.py`.** Recorded because the audit reported them as missing and that finding is wrong.
  - **Why:** The audit's file search did not return zero-byte files, so the 32 empty `__init__.py` markers looked absent. They have in fact been tracked since commit `98b7087` ("rename platform package to relay"), covering `relay/`, `relay/api/`, `relay/infrastructure/` and its subpackages, `relay/data_plane/` and its subpackages, and the forbidden trees. `git ls-files | grep __init__` is the check. No PEP 420 namespace-package fragility exists, and `[tool.setuptools.packages.find]` needs no adjustment. Left as-is; the `migrations/` Alembic script directory correctly has none.

- **Decision 4 — `pytest` declared as a `dev` extra, not a runtime dependency:** `[project.optional-dependencies] dev = ["pytest", "pytest-asyncio"]`, installed with `pip install -e ".[dev]"`. Also added `[tool.pytest.ini_options]` with `asyncio_mode = "auto"`.
  - **Why:** `pytest` was installed nowhere and declared nowhere, so the existing 80 Person B/C tests could not be run from a clean checkout. The sprint rule limits `pyproject.toml` to adding `httpx`; an optional extra declares the tool without touching the runtime dependency list. See `data-plane-test-suite.md`.

- **Decision 5 — Worker timeout is configurable:** `WEBHOOK_TIMEOUT_SECONDS`, default 5.0, read via `_default_timeout_seconds()`.
  - **Why:** The audit noted that the Simulator's `slow` mode with `latency_ms > 5000` exceeds the client timeout and is recorded as `timed_out=True`, making `slow` and `timeout` indistinguishable. The Worker's behaviour is correct — it observes HTTP status, timeout, and latency and nothing else (`webhook-worker.md` Decision 1) — and `simulator/` is Person C's. An env var lets a test widen the window without touching either.

## Environment variables introduced

| Variable | Default | Purpose |
|---|---|---|
| `RELAY_ENV` | `dev` | Gates DB credential fallback and API auth enforcement |
| `RELAY_API_KEYS` | unset | `owner_id:secret` pairs (ADR 0018) |
| `RELAY_ALLOW_PRIVATE_TARGETS` | `false` | Permits private/loopback delivery targets (ADR 0017) |
| `RELAY_WEBHOOK_SIGNING_SECRET` | unset | HMAC key when `integrity_policy.hmac_required` (ADR 0016) |
| `WEBHOOK_TIMEOUT_SECONDS` | `5.0` | Worker client timeout |
| `DISPATCHER_MAX_CONCURRENT_MESSAGES` | `16` | Concurrent business messages |
| `DISPATCHER_MAX_IN_FLIGHT_RETRIES` | `500` | Retries awaiting backoff |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `20` / `20` | Engine pool sizing |
| `LOG_LEVEL` | `INFO` | Dispatcher `logging.basicConfig` |

Pre-existing and unchanged: `DATABASE_URL`, `ASYNC_DATABASE_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC_PARTITIONS`, `KAFKA_TOPIC_REPLICATION_FACTOR`, `SIMULATOR_URL`, and the `FeatureFlags` set.

`scripts/week34_smoke.py` sets `RELAY_ALLOW_PRIVATE_TARGETS=true` via `os.environ.setdefault` before the `relay` imports, since the Simulator is on `127.0.0.1` and the guard reads the variable at call time.
