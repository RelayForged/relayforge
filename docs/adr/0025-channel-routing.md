# ADR 0025: Channels for Per-Tenant Routing

## Status

Accepted (Person B, 2026-10-14). Needs Person A's review: it adds a migration.

## Context

A subscription matched on event type only. A platform that sends webhooks on behalf of many customers cannot work like that. A payment gateway publishes `payment.captured` for every merchant, and every merchant subscribed to `payment.captured` would receive every other merchant's payments.

The endpoint's `owner_id` cannot do this job. `POST /endpoints` sets it to the authenticated API-key holder (`relay/api/endpoints.py`). The tenant of Relay is the gateway, and its merchants sit below that, with no identity in Relay.

## Decision

Events and subscriptions may carry **channels**, short string tags such as a merchant id. This is the model Svix uses.

**The rule:**
- A subscription with **no channels** receives every event of its types. This is the previous behaviour, so every existing subscription is unchanged.
- A subscription **with channels** receives an event only if they share at least one channel. An event with no channels does not reach it.

**Implementation:**
- **Schema:** `events.channels` and `subscriptions.channels` are `TEXT[] NOT NULL DEFAULT '{}'`, with a GIN index on `subscriptions.channels`. Migration `d4e1c7a9b2f0` is reversible.
- **Fan-out:** the Dispatcher passes `event.channels` to `SubscriptionRepository.list_active_targets_by_event_type`, which filters in the same query with `cardinality(channels) = 0 OR channels && :event_channels`. `Subscription.receives()` states the same rule in Python, for fakes and readers.
- **API:** `POST /events` and `POST /subscriptions` accept optional `channels`: at most 10, each 1–64 characters of `[A-Za-z0-9_.:-]`, de-duplicated.
- **Replay:** the replay Event copies the original's channels.
- **Immutability:** channels are part of the immutable Event and are set on insert, so the events append-only trigger is unaffected.

## Consequences

- One Relay tenant can serve many sub-tenants with isolated delivery. Demo 2 relies on this.
- **Channels are routing, not authorization.** Nothing stops an API-key holder from subscribing an endpoint to another channel, or from creating a subscription for an endpoint it does not own. `POST /subscriptions` does not check endpoint ownership today. Per-tenant authorization is a separate change.
- AEDE is untouched. Health, state and policy remain per endpoint.
