# ADR 0002: Adopt At-Least-Once Delivery Semantics

## Status

Accepted

## Context

The platform must choose a delivery semantic: exactly-once, at-least-once, or at-most-once.

**At-most-once** (fire and forget) is immediately ruled out. An event delivery platform that accepts losing events is not a reliable delivery platform.

**Exactly-once** appears desirable — deliver each event to each endpoint precisely one time, with no duplicates. In practice, exactly-once delivery across distributed external boundaries is not achievable without cooperation from both sides of the communication channel.

The fundamental problem is that acknowledging successful delivery requires a two-phase commit across organizational boundaries:

1. The platform delivers the event and receives an HTTP 200 response.
2. The platform marks the event as delivered.

If the platform crashes between step 1 and step 2, it cannot know whether delivery succeeded. On recovery, it must retry — producing a duplicate delivery.

Conversely, if the endpoint crashes after processing the event but before returning an HTTP 200, the platform will retry — producing a duplicate delivery.

There is no distributed transaction that prevents this. Webhook endpoints are owned by external organizations. They cannot be enrolled in a two-phase commit protocol. TLS termination, request idempotency, and transactional acknowledgment are not guaranteed by external systems.

Any claim of exactly-once delivery in this context would require either:
- Accepting potential event loss (violating reliability), or
- Trusting endpoint acknowledgment without verification (not truly exactly-once), or
- Building complex deduplication infrastructure inside each subscriber (moving complexity to consumers).

## Decision

Adopt **at-least-once delivery semantics**.

The platform guarantees that every event will be delivered to its endpoint at least once. Under failure conditions, the same event may be delivered more than once. Consumers are required to implement idempotent processing.

To assist consumers, the platform:
- Assigns a globally unique, stable Event ID to every event.
- Includes the Event ID in every delivery attempt as a header.
- Provides Delivery Attempt IDs for deduplication logging.
- Documents idempotency requirements clearly in the SDK and API documentation.

## Consequences

**Positive:**
- Greatly simplifies the platform's delivery infrastructure. Workers do not need distributed locking or two-phase commit protocols.
- Recovery from worker failure is straightforward: re-deliver from the last committed Kafka offset.
- The platform can guarantee that no event is silently lost — the stronger half of the exactly-once promise.
- Aligns with how production webhook platforms (Stripe, GitHub, Shopify) universally operate.

**Negative:**
- Consumers may receive duplicate events during failure scenarios (worker crash mid-delivery, network timeout with successful endpoint processing, etc.).
- Consumers must implement idempotent handlers, or accept the operational risk of duplicate side effects.
- The platform cannot provide exactly-once guarantees to consumers that do not support idempotency (e.g., external payment processors without idempotency keys).

**Accepted trade-offs:**
- Duplicate delivery is a bounded, observable, recoverable problem. Event loss is not.
- The operational cost of duplicate handling is lower than the operational cost of distributed transaction management across external system boundaries.
- The research contribution (AEDE) focuses on reducing unnecessary retries — which reduces the incidence of duplicates — without requiring exactly-once infrastructure.

This decision is consistent with industry practice. Stripe, GitHub, Twilio, and all major webhook providers adopt at-least-once delivery and require idempotent consumers.
