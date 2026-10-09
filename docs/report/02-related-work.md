# Chapter 2 — Related Work & Literature Gap

## 2.1 Research Context

This work sits at the intersection of five research domains, drawing from each without fitting
entirely inside any one of them.

**Distributed Systems.** Relay applies distributed systems principles — resilience, graceful
degradation, eventual consistency — to outbound event delivery. Rather than assuming endpoints are
available, the system assumes they will eventually fail, and is designed around that assumption from
the outset.

**Event-Driven Architecture.** Relay extends EDA principles beyond internal service-to-service
communication by treating outbound webhook delivery as another stage in a durable event pipeline,
rather than a side effect bolted onto application code.

**Stream Processing.** The platform adopts Kafka's durable log model directly: workers consume
immutable events from a durable log, which is what makes deterministic replay and simplified failure
recovery possible in the first place (ADR 0001).

**Cloud-Native Infrastructure.** Containerisation and infrastructure-as-code practices are used to keep
the platform reproducible across environments, though Kubernetes and Terraform are explicitly deferred
from the MVP scope (Chapter 3).

**Developer Experience.** SDKs, a live dashboard, replay tooling, and observability are treated as
first-class deliverables rather than optional extras — motivated directly by Principle 9
(architecture.md): if developers repeatedly solve the same infrastructure problem manually, the
platform should eventually solve it for them.

## 2.2 Literature Position

Prior work relevant to this problem falls into five categories, each of which solves part of the
problem this work addresses, but not the specific combination.

1. **Adaptive Retry and Circuit Breaking.** Adaptive controllers in service meshes outperform static
   retry policies by adjusting backoff based on observed runtime behaviour. These systems, however,
   assume control over — or at minimum, deep instrumentation of — both communicating services.

2. **Endpoint Health and Failure Detection.** Statistical and ML-based anomaly detection techniques
   achieve high accuracy, but rely on telemetry from fully instrumented, operator-controlled
   infrastructure — traces, sidecar proxies, kernel-level signals — none of which are available when the
   destination is a third party's webhook endpoint.

3. **Distributed Messaging and Stream Processing.** Systems such as Kafka solve durable event storage,
   partitioning, and replay extremely well, and this work builds directly on that foundation. These
   systems intentionally stop at reliable transport; they make no claims about what should happen at the
   delivery-policy layer above them.

4. **Cloud Security.** Mature, well-established approaches exist for authentication, message integrity,
   cryptographic signing, and replay prevention. These operate independently of — and are largely
   orthogonal to — runtime delivery-policy decisions, and this work does not attempt to advance them.

5. **Developer Tooling.** Commercial webhook platforms (Svix, Hookdeck, and similar) provide
   production-ready delivery infrastructure and dashboards, and likely implement some form of adaptive
   behaviour internally. Their implementations are proprietary: there is no published account of their
   internal decision architecture, policy models, or comparative evaluation against simpler baselines.

## 2.3 The Compound Gap

Each individual problem domain above has received substantial attention on its own. What remains
largely unexplored in the **published literature** — as distinct from what may exist in unpublished,
proprietary commercial systems — is the integration of these concepts into a single, unified runtime
decision framework designed specifically for outbound event delivery, together with an experimental
validation of that framework against conventional approaches.

This distinction matters and is stated precisely rather than overstated: the gap addressed here is a
**research literature gap**, not a claim that no comparable system has ever been built. Commercial
webhook platforms plausibly implement adaptive delivery behaviour internally; without a published
account of their architecture or a comparative evaluation, that possibility cannot be verified or
built upon by other researchers. To the best of the author's knowledge, no prior published work:

- presents a unified policy framework that derives delivery behaviour from continuously evaluated
  endpoint state using only externally observable signals,
- experimentally compares that framework against static and adaptive baselines under controlled,
  reproducible failure conditions, **and**
- publishes the complete implementation — infrastructure, configuration, and benchmark tooling —
  for independent verification and extension.

## 2.4 Why Existing Adaptive Systems Cannot Be Directly Applied

Adaptive controllers from the service-mesh literature cannot simply be repointed at webhook delivery.
Four constraints specific to this problem domain explain why, and motivate AEDE's design directly.

**Constraint 1 — External Ownership.** Adaptive controllers in service meshes assume control over both
communicating services. Webhook delivery targets systems owned by external organisations whose
infrastructure cannot be modified, instrumented, or controlled in any way.

**Constraint 2 — Limited Observability.** Service meshes rely on distributed traces, sidecar proxies,
and kernel-level telemetry. Webhook platforms observe only externally visible signals — HTTP response
codes, response latency, connection timeouts, TLS errors — and must infer endpoint state from
significantly weaker signals than the adaptive-controller literature assumes are available.

**Constraint 3 — No Traffic Control.** When an internal service degrades, a service mesh can reroute
traffic or drain instances. A webhook endpoint offers no such lever: the delivery platform can only
decide *whether*, *when*, and *how frequently* to attempt delivery — it cannot redirect traffic
elsewhere, because there is no "elsewhere."

**Constraint 4 — External Failure Amplification.** Aggressive retries against an internal service
consume infrastructure the operator owns and can scale. Aggressive retries against a webhook endpoint
directly impact a third party's infrastructure, and may amplify an outage already in progress or
violate a rate limit the operator has no visibility into. The platform therefore has an obligation to
balance reliability against responsible delivery behaviour that a service mesh, operating entirely
within one organisation's infrastructure, does not have to consider.

These four constraints are the reason AEDE is built around ERI (a composite of only externally
observable signals), a conservative state machine with asymmetric hysteresis (Chapter 3 §3.4), and an
isolation mechanism (quarantine) that actively stops delivery rather than merely rerouting it — each a
direct response to one of the constraints above, not an arbitrary design choice. Chapter 5 §5.8
evaluates, via ablation, how much each of these specific design responses actually contributes to the
measured outcome, rather than assuming the design is correct because the constraints motivating it are
real.
