# ADR 0017: Guard Outbound Delivery Targets Against Internal Network Access

**Status:** Accepted

**Context**

`Endpoint.target_url` is an arbitrary client-supplied string. `CreateEndpointRequest` declared it as `target_url: str` with no validation, and the webhook Worker is the process that dereferences it. That made the data plane a server-side request forgery proxy (CWE-918): any caller could register `http://169.254.169.254/latest/meta-data/`, `http://localhost:5432`, or `file:///etc/passwd`, then trigger the fetch with `POST /events`.

The consequences compounded. `http_status` and `error_message` are persisted on `DeliveryAttempt` and surfaced through the dashboard adapter, giving a blind-SSRF read channel. And a non-HTTP scheme raised `httpx.InvalidURL`, which does not inherit from `httpx.HTTPError` and so escaped the Worker's handlers and killed the Dispatcher.

Validation cannot live on the entity: `Endpoint` is frozen kernel, read-only for the data plane (ADR 0010). It also cannot live only at registration, because a hostname that resolves to a public address at registration time can be re-pointed at a link-local metadata address afterwards (DNS rebinding).

Local development complicates this: the Simulator runs on `127.0.0.1:8000`, which any correct guard must block by default.

**Decision**

Introduce `relay/infrastructure/url_guard.py` as the single egress policy for outbound targets, applied at two points.

1. **`check_url()` at registration** (`relay/api/schemas.py`) — no DNS. Rejects schemes other than http/https, missing hosts, embedded credentials, known metadata hostnames (`localhost`, `metadata.google.internal`, `metadata.goog`, `instance-data`, …), IP literals that are private, loopback, link-local, reserved, multicast, or unspecified, and URLs over 2048 characters. Surfaces as HTTP 422.

   The `is_*` properties alone are not sufficient. CPython 3.12 does not classify RFC 6598 carrier-grade NAT space (`100.64.0.0/10`) under any of them, yet `100.100.100.200` is Alibaba Cloud's metadata endpoint and the range is widely used for internal infrastructure. `BLOCKED_NETWORKS` lists such ranges explicitly. IPv4-mapped (`::ffff:127.0.0.1`), NAT64 (`64:ff9b::a9fe:a9fe`), and 6to4 (`2002:7f00:1::`) encodings of internal addresses were verified to be caught already by `is_private` / `is_reserved`.

2. **`assert_deliverable()` before every POST** (`relay/data_plane/workers/webhook.py`) — `check_url()` plus `loop.getaddrinfo()`, checking every resolved address. Catches rebinding and any target row that predates this guard. A block is recorded as a failed `DeliveryAttempt`, never raised into the consumer loop.

3. **Redirects stay disabled** on the shared `httpx.AsyncClient`. Following a redirect is a trivial guard bypass.

4. **`RELAY_ALLOW_PRIVATE_TARGETS=true` disables the address checks only.** Scheme, host-presence, and credential checks always apply. `scripts/week34_smoke.py` sets it so the loopback Simulator stays reachable.

5. **`httpx.InvalidURL` is caught explicitly** alongside `HTTPError`, and a catch-all records an unexpected error as a failed attempt rather than letting it propagate.

**Consequences**

Positive:
- The metadata service, loopback services, and RFC1918 ranges are unreachable from the delivery path by default.
- Registration fails fast with 422 instead of producing a stored row that fails mysteriously at delivery time.
- Re-checking on every attempt means the guard cannot be bypassed by rows created before it existed, or by a DNS record changed after registration.

Negative:
- One `getaddrinfo` per delivery attempt. It is async and the OS resolver caches, but it is not free.
- The check is still TOCTOU: `getaddrinfo` and the connection are separate operations. Closing that hole entirely requires pinning the resolved address into a custom transport, which is deferred.
- `BLOCKED_NETWORKS` is a manual list and will drift as new cloud providers publish metadata endpoints outside the ranges `ipaddress` flags. It is pinned by `tests/data_plane/test_url_guard.py` so a regression is caught, but additions are a judgement call.
- `RELAY_ALLOW_PRIVATE_TARGETS` is a genuine footgun. It is documented as dev-only and must never be set anywhere that accepts untrusted endpoint registrations.
- A legitimate subscriber inside a private network cannot be reached without the escape hatch. Such deployments need an allowlist rather than a blanket bypass.
