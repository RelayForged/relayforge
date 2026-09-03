# Decision: Request size limits and boundary validation on the ingestion API

- **Context:** `IngestEventRequest.payload` was `Dict[str, Any]` with no bound and no request-size limit in front of it. Authentication is ADR 0018; the SSRF guard on `target_url` is ADR 0017. This note covers the remaining boundary limits.

- **Decision 1 — `payload` capped at 256 KiB, measured on the serialized form:** a `field_validator` re-serializes with `json.dumps(..., separators=(",", ":"), default=str)` and rejects over the limit.
  - **Why:** Two failure modes, not one. The obvious one is memory. The dangerous one is that an oversized `payload` commits fine to `JSONB` and *then* fails to publish, because the serialized Event exceeds Kafka's default 1 MiB `max.request.size` — producing a persisted event that can never be published, which persist-first (ADR 0002) has no recovery path for. 256 KiB leaves ample room for the Event envelope. Measuring the serialized size rather than counting keys is what actually predicts the Kafka record size.

- **Decision 2 — A separate ASGI middleware caps the whole request at `payload + 64 KiB`:** `BodySizeLimitMiddleware` checks `Content-Length` and also counts streamed `http.request` chunks, returning 413 (or 400 for an unparseable `Content-Length`).
  - **Why:** Decision 1 can only run *after* the body has been fully read and parsed into memory, so on its own it does not prevent the memory cost it is meant to bound. Chunked requests declare no `Content-Length`, hence the streaming counter as well.

- **Decision 3 — Written as raw ASGI, not `BaseHTTPMiddleware`:** implemented as a class with `__call__(scope, receive, send)` and registered via `app.add_middleware`.
  - **Why:** `@app.middleware("http")` wraps every request in `BaseHTTPMiddleware`, which runs the downstream app in a separate anyio task. That is unnecessary overhead on the hot ingestion path for something that only needs to inspect headers, and it complicates cancellation. The first implementation used the decorator and produced a confusing asyncpg "attached to a different loop" error under `TestClient`; the raw-ASGI version avoids the whole question.

- **Decision 4 — Scalar bounds on the remaining DTO fields:** `event_type` 1–255 chars, `owner_id` <= 255, `endpoint_id` <= 64, `target_url` <= 2048, `event_types` <= 100 entries each 1–255 chars.
  - **Why:** `event_type` reaches an indexed column and the Kafka topic key; `event_types` reaches a `varchar[]` that the GIN index has to cover. All were previously unbounded strings. These are DTO-only, so no kernel entity changes (ADR 0010).

- **Decision 5 — Bounds live on the DTOs, not the entities:** all of the above are in `relay/api/schemas.py`.
  - **Why:** Kernel models are frozen and read-only for the data plane. The DTO boundary is the only place Person A can validate, and it is also the correct place — these are transport limits, not domain invariants.
