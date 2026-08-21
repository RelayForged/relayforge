# Decision: FastAPI Ingestion App Shape

- **Context:** Week 2 Step 2 required `POST /events`, `GET /events/{id}`, `POST /endpoints`, `POST /subscriptions` with persist-first and no HTTP retry logic.

- **Decision 1 — Request DTOs vs domain entities:** `IngestEventRequest` is only `event_type` + `payload`. The API constructs `CorrelationID`/`CausationID` and the frozen `Event`. Clients must not supply those IDs in Week 2 (required-fields-only validation).
- **Decision 2 — 202 only after persist AND publish:** Order is validate → `EventRepository.save()` → `publish()` → 202. If Postgres fails, Kafka is never called and the client does not get 202. If Kafka fails after persist, return **503** with `event_id` in the body — the row is already durable (ADR 0002), but 202 would claim the pipeline accepted the event. The route does **not** retry Kafka (ADR 0005). Republish recovery is deferred.
- **Decision 3 — POST /subscriptions resolves endpoint_id:** Lookup via `EndpointRepository.get_by_id` and return 404 if missing, with `IntegrityError` as a race fallback. Avoids leaking a raw FK error as 500.
- **Decision 4 — Lifespan only closes the producer:** Matches the lazy-start wrapper. `uvicorn relay.api.main:app --host 0.0.0.0 --port 8000`.
