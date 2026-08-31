# Decision: Webhook Worker — strict httpx timeout, Telemetry + attempt completion

- **Context:** Phase 4 requires a stateless worker: `httpx.post` to `endpoint.target_url`, Simulator modes, persist `Telemetry` (ERI / TelemetryProtocol) and completed `DeliveryAttempt`. No retry/backoff.

- **Decision 1 — Client timeout 5s (`httpx.Timeout`), measured with `perf_counter`:** Simulator `timeout` sleeps `latency_ms` then would return 200; the worker must abort first (`timed_out=True`, `http_status=None`). `slow` sleeps but finishes inside 5s (`succeeded=True`, high `latency_ms`). `http_error` → 5xx, `rate_limit` → 429; both `succeeded=False` (success is HTTP 2xx only).
  - **Why:** Worker never interprets Simulator config. It only observes HTTP status / timeout / latency.

- **Decision 2 — Attempt completion is `model_copy` + `save()` upsert of outcome columns:** Dispatcher already inserted attempt 1 (persist-first). `ON CONFLICT (attempt_id) DO UPDATE` fills `completed_at` / `http_status` / `success` / `error_message` only while `completed_at IS NULL`. Alembic `6b999f7451a9` narrows the attempt trigger to DELETE-only. Telemetry stays insert-only (`ON CONFLICT DO NOTHING`).
  - **Why:** Frozen kernel forbids mutating the Pydantic instance. A second HTTP on the same attempt_id must not rewrite Telemetry or a completed attempt.

- **Decision 3 — `execute(attempt, target_url, endpoint_id)`:** `endpoint_id` is required for `Telemetry` (not on `DeliveryAttempt`). No Event body this phase (Simulator `/webhook` ignores payload). No backoff.
