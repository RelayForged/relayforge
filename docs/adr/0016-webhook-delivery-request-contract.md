# ADR 0016: Webhook Delivery Request Contract

**Status:** Accepted

**Context**

Through Week 3–4 the Worker called `client.post(target_url)` with no body, no `Content-Type`, and no headers. A subscriber received a zero-length POST and had no way to learn which event it represented. The Simulator returns its configured status regardless of the request, so `scripts/week34_smoke.py` passed green and hid this completely.

This is not merely a missing feature. ADR 0002 lists concrete obligations the platform took on to make at-least-once workable for consumers:

- "Includes the Event ID in every delivery attempt as a header."
- "Provides Delivery Attempt IDs for deduplication logging."

Neither was implemented. Without them a consumer cannot deduplicate, which is precisely what ADR 0002 requires consumers to do.

`docs/small-decisions-personA/webhook-worker.md` Decision 3 recorded "No Event body this phase (Simulator `/webhook` ignores payload)". That was a defensible Phase-4 scoping call, but it cannot survive contact with a real subscriber.

Two constraints shape the fix. The `Endpoint` kernel entity has no per-endpoint secret field and the kernel is frozen and read-only for the data plane (ADR 0010), so signing keys cannot live on the endpoint. And `DeliveryPolicy.integrity_policy` already specifies `hmac_required`, `signature_ttl_seconds`, and `replay_protection` — ADR 0005 says the Worker executes policy mechanically rather than interpreting it, so those fields must actually be honored.

**Decision**

1. **The serialized Event is the request body.** `event.model_dump_json()` with `Content-Type: application/json`. The Event is already the platform's immutable business fact (ADR 0010); wrapping it in a second envelope would invent a shape the kernel does not define.

2. **Delivery metadata travels in headers**, so a subscriber can deduplicate without parsing the body:
   - `X-Relay-Event-Id`, `X-Relay-Event-Type`
   - `X-Relay-Delivery-Id`
   - `X-Relay-Attempt-Id`, `X-Relay-Attempt-Number`
   - `User-Agent: RelayForge/0.1`

3. **`RetryWork` carries the `Event`.** A retry must reproduce the byte-identical request, and the Dispatcher consuming `events.internal.delivery` has no other handle on it.

4. **HMAC signing is executed from `integrity_policy`, not decided by the Worker.** When `hmac_required` is set, the Worker signs `timestamp.nonce.body` with SHA-256 and sends `X-Relay-Signature: sha256=<hex>`, plus `X-Relay-Timestamp` and, when `replay_protection` is set, `X-Relay-Nonce`. The timestamp and nonce are inside the signed material — signing only the body would leave both as headers an attacker can rewrite freely.

5. **The signing key is process-wide, from `RELAY_WEBHOOK_SIGNING_SECRET`.** Per-endpoint secrets need a kernel field that does not exist; that is Person B's call.

6. **A required-but-unconfigured secret fails the attempt.** The Worker records `error_message` and returns rather than delivering unsigned. Silently downgrading an integrity requirement is worse than a failed delivery.

**Consequences**

Positive:
- Deliveries carry the event. ADR 0002's consumer-side deduplication obligations are met.
- Strategy A sets `hmac_required=False`, so signing is inert in Week 3–4 and activates the moment a policy turns it on, with no Worker change.
- `execute()` now requires `event`, so a caller cannot reintroduce an empty-body delivery by omission.

Negative:
- `WebhookWorker.execute` gained a required keyword-only `event` parameter and `RetryWork` gained an `event` field. Both are Person A-owned and have no callers outside `relay/data_plane/`, but the retry payload is now larger on `events.internal.delivery`.
- One global signing secret means all subscribers verify with the same key. Acceptable while `hmac_required` is false everywhere; it must become per-endpoint before HMAC is switched on in anger.
- `signature_ttl_seconds` is published as `X-Relay-Timestamp` for the subscriber to enforce; the platform does not enforce it outbound.
- The Simulator ignores the body, so the smoke test cannot assert on it. Body correctness is covered by transport-level assertions instead.

**Amends**

Decision 3 of `docs/small-decisions-personA/webhook-worker.md`.
