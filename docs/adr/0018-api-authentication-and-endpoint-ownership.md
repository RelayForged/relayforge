# ADR 0018: Authenticate the Ingestion API and Derive Endpoint Ownership From the Caller

**Status:** Accepted

**Context**

`relay/api/main.py` mounted all four routes with no authentication, authorization, or rate limiting (CWE-306). Anyone who could reach the port could:

- inject events into the append-only durable log, which no code path can delete (ADR 0011);
- register webhook endpoints, which is the SSRF sink ADR 0017 addresses;
- read any event's full payload via `GET /events/{event_id}`.

Ownership was also self-asserted. `CreateEndpointRequest.owner_id` was a client-supplied string written straight to the `endpoints` row, so any caller could register an endpoint under another tenant's `owner_id`. `ix_endpoints_owner_id` exists specifically to query by owner, so the field is load-bearing, not decorative.

The constraint is that authentication must not break the existing local flows. `scripts/api_smoke.sh` drives the API with plain `curl` and no credentials, and local `uvicorn` runs need to keep working.

**Decision**

1. **A shared-secret `X-API-Key` dependency on every route.** `RELAY_API_KEYS` holds comma-separated `owner_id:secret` pairs, parsed once and cached. Keys are compared with `hmac.compare_digest` so a wrong key cannot be recovered by timing the comparison.

2. **`owner_id` comes from the authenticated principal.** `POST /endpoints` records the resolved owner and ignores the body value whenever a real key authenticated the request. `CreateEndpointRequest.owner_id` becomes optional and is honored only in unauthenticated dev mode.

3. **Environment-gated enforcement.** With `RELAY_ENV` unset or `dev` and no keys configured, requests proceed as the synthetic principal `dev-local`. With `RELAY_ENV` set to anything else and no keys configured, every request returns 503 and logs an error.

4. **Authentication only; no authorization model yet.** Any valid key may ingest events and register endpoints, and `GET /events/{event_id}` is not scoped to an owner, because `Event` carries no owner field and the kernel is frozen.

**Consequences**

Positive:
- The durable log and the SSRF sink both require a credential.
- Ownership is no longer spoofable, so `owner_id` becomes trustworthy for future per-tenant quotas and dashboards.
- A deployment cannot silently ship unauthenticated: the failure is a loud 503 rather than an open door.
- `api_smoke.sh` and local uvicorn keep working untouched.

Negative:
- Shared secrets in an environment variable, not signed tokens. No expiry, no rotation, no scopes. Adequate for a research platform, not for multi-tenant production.
- Dev mode being open by default means a developer who exposes their laptop's port is exposed. Loopback-only port binding (ADR-adjacent, see `docs/small-decisions-personA/compose-dev-hardening.md`) reduces but does not remove this.
- `GET /events/{event_id}` lets any authenticated caller read any event by ID. IDs are uuid4 and therefore unguessable, but this is authentication substituting for authorization and needs an owner column on `events` to fix properly — a kernel change for Person B.
- Keys are cached for the process lifetime, so rotation requires a restart. Tests call `_configured_keys.cache_clear()`.
