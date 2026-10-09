# AEDE: A Deterministic, Explainable Policy Engine for Adaptive Webhook Event Delivery

<!-- TODO(Sam): resolve authorship/affiliation before submission -->
<!--
PLACEHOLDER AUTHOR BLOCK — replace before submission.
Name, affiliation, and email below are placeholders inserted by the paper-drafting process.
-->
Author Name¹

¹[Affiliation]

sam@clearlyblue.in

## Abstract

Webhook delivery — how distributed systems notify external endpoints of business events — is
typically a naive HTTP request wrapped in a fixed retry loop, discarding all history of an endpoint's
observed behavior between attempts. This paper presents the Adaptive Event Delivery
Engine (AEDE), a deterministic, rule-based control-plane component that derives a composite Endpoint
Reliability Index (ERI) from externally observable signals only, drives a five-state endpoint state
machine with asymmetric hysteresis, and synthesizes immutable delivery policies for a stateless data
plane to execute directly. AEDE is evaluated across 14 fault-injection scenarios against a static-retry
baseline and an adaptive-backoff-without-state baseline, and, within a held-out-subset ablation study,
against an off-the-shelf Hystrix-style circuit breaker, using a leakage-free calibration/held-out split
and five-seed replication. On the held-out evaluation set, AEDE
reduces wasted retries by 86.9% relative to static retry and 77.1% relative to adaptive backoff (paired
two-sample $t$-test, $p<0.001$). This ablation shows the reduction comes primarily from the isolation
mechanism rather than from state-awareness alone, while asymmetric hysteresis contributes stability —
far fewer state transitions — rather than additional retry savings; the circuit-breaker baseline
recovers 70.7% of the outage-retry reduction but requires 5.9$\times$ more state transitions and
14.0$\times$ more trips than AEDE, reinforcing the same stability finding outside this codebase. All 28
weight-sensitivity perturbations remain stable, with outage-retry reduction never falling below 81.8%.
The evaluation is disclosed as synthetic and single-endpoint; AEDE is not a machine-learning system,
and its offline genetic-algorithm-calibrated weights are a one-time, disclosed hyperparameter search
rather than a learned runtime mechanism.

**Index Terms**—webhook delivery, adaptive retry, circuit breaking, endpoint reliability index, state
machine hysteresis, explainability

---

## I. Introduction

Modern distributed systems rely on webhooks to notify external systems of business events — an order
placed, a payment settled, a subscription renewed. Webhook delivery crosses organizational and
infrastructural boundaries the sender does not control: network latency, endpoint availability,
deployment schedules, and rate limits are all outside the sender's authority. Failures are therefore
expected, not exceptional. Despite this, webhook delivery is typically implemented as a bare HTTP
request followed by a fixed retry loop that discards all information about the endpoint's observed
behavior between attempts. As applications scale, developers independently rebuild the same
supporting infrastructure — retry queues, replay mechanisms, dead-letter queues, dashboards — with
inconsistent reliability guarantees and no shared understanding of *why* a given retry policy behaved
the way it did.

This paper is built on Relay, an open-source event-delivery platform that reframes webhook delivery as
a distributed-systems problem rather than an HTTP problem: applications publish immutable events, and
the platform owns everything after that — scheduling, delivery, retry, replay, failure isolation, and
observability. Durable delivery infrastructure of this kind is a largely solved engineering problem;
systems built around a durable, replicated log such as Apache Kafka [1] and commercial webhook
platforms built on similar primitives already do it well. What remains
comparatively unaddressed in the published literature is the *policy* question layered on top of that
infrastructure: given only externally observable signals about an endpoint's behavior, what should the
delivery policy be at this moment, and can that decision be made deterministically, explainably, and
demonstrably better than conventional alternatives?

**Research question.** Can adaptive runtime delivery policies driven by continuously evaluated
endpoint state outperform conventional static retry mechanisms in webhook-based event delivery
systems, under realistic partial-failure conditions?

**Research hypothesis.** Runtime delivery policies derived from continuously evaluated endpoint state
can improve delivery efficiency, reduce unnecessary retries, decrease administrative intervention, and
increase system resilience compared with conventional static retry strategies, while maintaining
complete transparency and reproducibility. This is deliberately a systems/architecture hypothesis, not
a claim about any particular numeric configuration: the object of study is whether coupling continuous
endpoint-state evaluation to policy synthesis improves delivery outcomes, not whether any specific set
of weights or thresholds is optimal.

**The proposed system.** The Adaptive Event Delivery Engine (AEDE) is the control-plane component that
answers this question empirically. It derives an Endpoint Reliability Index (ERI) — a deterministic
composite score computed only from externally observable signals (HTTP status codes, latency,
timeouts, connection failures) — drives a five-state endpoint state machine with hysteresis, and
synthesizes an immutable `DeliveryPolicy` object that a stateless data plane executes without making
any runtime decisions of its own. Every synthesized policy is accompanied by an `ExplainabilityRecord`
documenting the metrics, thresholds, and reasoning that produced it. AEDE is deliberately not a
machine-learning system: no model is trained and no parameters are learned from live traffic. Given
identical telemetry, AEDE always produces identical policies. Where a genetic-algorithm search is used
in this work, it is used offline, once, to calibrate a small number of disclosed hyperparameters — not
as a runtime learning mechanism.

**Contributions.** This work makes four contributions: (1) a state-driven model of endpoint health
derived entirely from externally observable signals, requiring no access to or cooperation from the
subscriber's infrastructure; (2) a policy framework that coordinates retry scheduling, endpoint
isolation, and hysteresis-based state stability through a single engine, evaluated directly via a
component ablation study that identifies *which specific piece* is responsible for which specific
outcome; (3) a reproducible, open-source implementation of the full platform, including
infrastructure, configuration, and benchmark tooling; (4) a controlled fault-injection and evaluation
framework — 14 scenarios, a leakage-free calibration/held-out split, multi-seed statistical testing,
weight sensitivity analysis, and an automated acceptance gate — reusable for future research into
adaptive event-delivery policies.

---

## II. Related Work

This work sits at the intersection of five research areas without fitting entirely inside any one of
them: distributed systems (resilience and graceful degradation applied to outbound event delivery),
event-driven architecture (treating webhook delivery as a stage in a durable event pipeline rather than
a side effect bolted onto application code), stream processing (adopting a durable append-only log
model so that deterministic replay and simplified failure recovery are possible), cloud-native
infrastructure (containerization and configuration-as-code for reproducibility), and developer
experience (SDKs, live dashboards, and observability as first-class deliverables).

Prior work relevant to this problem falls into five categories, each of which solves part of the
problem this work addresses but not the specific combination.

*Adaptive retry and circuit breaking.* Adaptive controllers in service meshes such as Envoy [2] and
Istio [3] outperform static retry policies by adjusting backoff based on observed runtime behavior —
the Circuit Breaker pattern, as popularized in production reliability engineering practice [4], [5],
is a well-established example. A recent line of work adapts the breaker mechanism itself rather than
treating it as static configuration: continuous-time Markov-chain modeling of several resiliency
patterns has shown a dynamically tuned open-state timeout outperforming fixed alternatives on the
availability-versus-overhead tradeoff [6]; a sensitivity-analysis-driven, AIMD-inspired controller
evaluated on Istio reports throughput gains of up to 12$\times$ under transient overload and 32$\times$
under noisy-neighbor contention relative to a static breaker [7]; a follow-on design derives the
open-state interval from queue length and an exponentially weighted moving average of response time
(analogous to TCP's smoothed round-trip time), improving success rate over [7]'s method at high load
(e.g., 65.43% versus 54.77% at 1000 requests/s) [8]; and an SLO-aware, load-adaptive timeout
controller built on a five-state operational model reports 40% lower average and 55% lower tail
latency [9]. These systems, however — including this more sophisticated recent generation — assume
control over, or at minimum deep instrumentation of, both communicating services, an assumption that
does not hold for third-party webhook endpoints.

*Endpoint health and failure detection.* Statistical and machine-learning-based anomaly detection
techniques [10] achieve high accuracy but rely on telemetry from fully instrumented, operator-controlled
infrastructure — distributed traces [11], sidecar proxies, kernel-level signals — none of which are
available when the destination is a third party's endpoint. Recent microservice-specific detectors
sharpen the same pattern: real-time feature-selection ensembles [12], clustering-plus-classifier
pipelines [13], and semi-supervised trace-graph analysis [14] all report high accuracy, but every one
is trained and evaluated on internally instrumented trace or metrics data that does not exist across an
organizational trust boundary. This gap is not merely theoretical. A large-scale study of partial
failures in production distributed systems found that 68% of real incidents were "sticky" failures that
conventional health probes never detected at all [15], and a controlled fault-injection study of
Kubernetes' own native liveness, readiness, and startup probes found they detected none of the injected
faults [16]. Together, these findings suggest that the externally visible, behavioral signal AEDE
relies on (Section III-C) is not simply a weaker substitute for instrumented monitoring — it targets
failure modes that conventional health checks miss even when those checks are available.

*Distributed messaging and stream processing.* Systems built around a durable, partitioned,
replayable log — the model popularized by Apache Kafka [1] — solve durable event storage and replay
well, and this work builds directly on that foundation. These systems intentionally stop at reliable
transport and make no claims about the delivery-policy layer above them.

*Cloud security.* Mature approaches exist for authentication, message integrity, cryptographic
signing, and replay prevention. These are largely orthogonal to runtime delivery-policy decisions, and
this work does not attempt to advance them.

*Developer tooling.* Commercial webhook delivery platforms such as Svix [17] and Hookdeck [18] provide
production-ready infrastructure and dashboards and likely implement some form of adaptive behavior
internally. Their implementations are proprietary: there is no published account of their internal
decision architecture, policy models, or comparative evaluation against simpler baselines.

**The compound gap.** Each of these domains has received substantial individual attention. What
remains largely unexplored in the *published* literature — as distinct from what may exist in
unpublished, proprietary commercial systems — is the integration of these concepts into a single,
unified runtime decision framework designed specifically for outbound event delivery, together with an
experimental validation of that framework against conventional approaches and a complete, openly
published implementation. This is stated precisely as a research-literature gap, not as a claim that no
comparable system has ever been built.

TABLE I summarizes this gap directly, contrasting AEDE against representative systems from the
categories discussed above. Cells are filled in based only on what is publicly documented about each
system; where a system's internals are proprietary and therefore genuinely unknown, the table says so
rather than guessing.

**TABLE I. AEDE VERSUS REPRESENTATIVE PRIOR-WORK CATEGORIES**

| System / Category | Control of Both Endpoints? | Deep Instrumentation (Traces/Sidecars)? | Published Comparative Evaluation? | Open-Source? | Discloses Calibration Methodology? |
|---|:---:|:---:|:---:|:---:|:---:|
| Envoy [2] (service mesh) | Yes | Yes (sidecar proxy) | No$^{c}$ | Yes | N/A (operator-configured, not calibrated) |
| Istio [3] (service mesh) | Yes | Yes (sidecar proxy + control plane) | No$^{c}$ | Yes | N/A (operator-configured, not calibrated) |
| ML/statistical anomaly detection [10], [12]–[14] | Typically assumed by deployments | Typically assumed (traces/metrics) | Not against webhook-delivery baselines specifically | Varies by technique | Rarely disclosed for deployed instances |
| Svix [17] | No | Not publicly documented | No | Partial$^{a}$ | No |
| Hookdeck [18] | No | Not publicly documented | No | Partial$^{b}$ | No |
| **AEDE (this work)** | **No** | **No (externally observable signals only)** | **Yes (Sections IV–V; 2 primary baselines + 1 external circuit-breaker ablation baseline, 14 scenarios, 5 seeds)** | **Yes** | **Yes (Section IV-E)** |

$^{a}$Svix's core webhook-sending service (`svix-webhooks`) is open source (MIT license); whether its
hosted platform contains adaptive decision logic beyond that core, and if so how it works, is not
publicly documented. $^{b}$Hookdeck's outbound component (`Outpost`) is open source (Apache-2.0
license); its hosted Event Gateway platform is not. $^{c}$This reflects the base mesh systems
themselves. Third-party adaptive controllers built on top of them do publish comparative evaluations
of their own designs [7], [8], but those evaluations remain within the bilateral, mesh-instrumented
setting and do not address the cross-organizational webhook case this table is about — reinforcing,
rather than undermining, the table's point.

No cell in this table is a criticism of the systems it lists — Envoy and Istio, in particular, solve a
different and harder problem (arbitrary internal service traffic) under different assumptions (control
of both sides). The table's purpose is narrower: to show precisely which combination of properties —
external-only observability, a published comparative evaluation against explicit baselines, and a
disclosed calibration methodology — remains simultaneously undemonstrated in the publicly available
record before this work.

**Why existing adaptive controllers cannot be directly applied.** Four structural properties of webhook
delivery explain why adaptive mesh controllers — including the more sophisticated recent designs
above, such as the sensitivity-analysis-driven breaker of Sedghpour et al. [7] and the queue- and
response-time-adaptive breaker of Sivakumar et al. [8], both evaluated on Istio — cannot simply be
repointed at it. First, these controllers assume bilateral instrumentation: a sidecar, or equivalent,
observing both the caller and the callee, which has no counterpart across a trust boundary to a
third-party subscriber. Second, their health signals are drawn from distributed traces and sidecar
telemetry gathered on both sides, whereas a webhook platform can only infer health unilaterally, from
HTTP-level signals alone — status codes, latency, timeouts. Third, a mesh can reroute traffic or drain a
degraded instance; a webhook platform has no authority to reconfigure or redirect a subscriber's
endpoint, and can only decide whether and when to retry. Fourth, a mesh's retry storms redistribute
load within infrastructure the operator already owns, while a webhook platform's retry storms land on
infrastructure it neither owns nor controls, adding a responsible-delivery obligation absent from the
mesh literature. TABLE I's first two columns summarize the first two of these properties directly.
Together, these four properties are the direct motivation for ERI (a composite of only externally
observable signals), a conservative state machine with asymmetric hysteresis, and an active isolation
mechanism (quarantine) rather than mere rerouting.

---

## III. System Design

### A. Architectural Overview

The platform is organized as a strict Control Plane / Data Plane separation: *the Control Plane
decides, the Data Plane executes*. The Control Plane (AEDE) continuously evaluates endpoint behavior,
computes ERI, drives the endpoint state machine, synthesizes immutable delivery policies, and produces
explainability records. The Data Plane ingests events, publishes them to a durable Kafka log [1],
dispatches them to stateless workers that execute a given `DeliveryPolicy` (HTTP requests, HMAC
signing, payload serialization), and collects telemetry — it never evaluates endpoint health or
computes retry intervals itself. This separation is enforced at the code level: policy logic never
appears in worker code, and workers receive a complete, immutable policy object that they execute
without interpretation. It yields four concrete benefits: independent scalability of workers versus
policy services, extensibility (new delivery transports require no policy-logic changes), testability
(identical telemetry always produces identical policies, and workers can be benchmarked independently
of policy logic), and explainability (the endpoint state and policy that produced any delivery attempt
can be inspected retroactively).

### B. Adaptive Event Delivery Engine (AEDE)

Conventional webhook delivery systems react at the request level: *request fails, wait, retry*. AEDE
instead reasons about endpoint behavior over time, asking what a failure implies about the endpoint's
current operational state rather than reacting to it in isolation — a single HTTP 503 is an
observation, but twenty consecutive 503 responses within a sliding window constitute evidence of
sustained degradation, and AEDE distinguishes between the two.

AEDE receives only externally observable telemetry: HTTP response codes and latency, connection
timeouts, TLS and DNS failures, consecutive failure/success streaks, and retry history and queue
depth. It has no access to subscriber infrastructure, sidecar proxies, or distributed traces. It
synthesizes an immutable `DeliveryPolicy` object containing six sub-policies — retry (initial delay,
max delay, max retries, backoff strategy), concurrency (max parallel workers, queue priority),
isolation (continue / reduce concurrency / pause / quarantine), replay (allowed, batch size, speed),
notification (admin alert level), and integrity (HMAC requirement). This object is the contract
between the Control Plane and the Data Plane; workers consume it without interpretation, and once
produced it is never modified.

**Determinism.** Given identical endpoint observations, identical configuration, and identical policy
rules, AEDE always generates identical delivery policies — a design requirement, not a guideline,
enforced by two mechanisms: AEDE never calls a wall-clock function for any policy-relevant computation
(time boundaries are always passed as explicit parameters by the caller), and no randomness appears
anywhere in ERI computation, state-machine transitions, or policy synthesis, all of which are pure
functions of their inputs. This determinism is what makes the Strategy A vs. B vs. C comparison in
Section V meaningful: the same telemetry sequence produces the same policy decisions across runs.

### C. Endpoint Reliability Index (ERI)

ERI is a deterministic composite score on a 0–100 scale, computed as a weighted sum of seven
normalized sub-metrics derived from externally observable signals: success rate, average response
latency — latency variability is well documented as symptomatic of underlying resource contention and
straggler effects in distributed systems [19], which motivates its inclusion here as an externally
observable proxy for endpoint-side degradation — length of the trailing consecutive-failure
streak, timeout rate, HTTP 5xx rate, retry-queue depth, and a three-valued recovery-trend indicator
comparing the first and second halves of the observation window (1.0 if the second half is better or
both are perfect, 0.5 if tied, 0.0 if the second half is worse). Weights sum to 1.0 by construction. ERI is the sole input to state-machine transitions, and is
computed over the last $N=100$ delivery attempts — a count-based rather than time-based window, chosen
specifically to preserve determinism: a time-based window (e.g., "last five minutes") would make two
evaluations at different wall-clock offsets include different telemetry and produce different scores
from identical history, breaking reproducibility. The tradeoff — that a count-based window responds
differently for high- versus low-volume endpoints — is accepted for this evaluation.

The illustrative default weighting (success rate 35%, latency 20%, consecutive failures 20%, timeout
10%, HTTP 5xx 5%, queue depth 5%, recovery trend 5%) is useful for explaining the mechanism but is
**not** the configuration used in the reported evaluation. The deployed weights are the output of an
offline, disclosed calibration process described in Section IV-E: `success_rate_weight ≈ 0.375` and
`consecutive_failures_weight ≈ 0.464` together carry over 80% of the composite score, with the
remaining five dimensions split unevenly — `recovery_trend_weight` capped at a regularization ceiling
of 0.150 (a deliberate design decision, described in Section IV-E, made after an unconstrained search
pushed this weight above 0.55), and the other four (latency, timeout, HTTP 5xx, queue depth) reduced to
a near-zero residual of $\approx 0.003$ each. Every ERI evaluation preserves all seven individual
sub-scores in a
`MetricSnapshot` embedded in the corresponding `ExplainabilityRecord`, enabling post-hoc analysis of
whether the composite score accurately reflected the endpoint's actual condition (Section VI-C).

### D. Endpoint State Machine and Hysteresis

Each endpoint exists in exactly one of five states at all times: **AVAILABLE** (normal operation, full
concurrency), **OBSERVED\_DEGRADATION** (early warning — moderate backoff, concurrency capped at 3),
**UNSTABLE** (persistent instability — aggressive backoff, single-stream delivery, admin notification),
**QUARANTINED** (delivery intentionally suspended — zero retries, events preserved, admin notified at
CRITICAL level), and **RECOVERING** (gradual, single-stream traffic restoration after evidence of
recovery). State transitions are driven exclusively by ERI.

Simple threshold-based transitions produce oscillation: an endpoint hovering near a boundary
alternates rapidly between states. Hysteresis prevents this by using different thresholds for
degradation versus recovery — trust failure quickly, trust recovery cautiously. In the calibrated
configuration used for evaluation, the degradation threshold is 75 (recovery from that state requires
ERI $>95$), the unstable threshold is 55 (recovery requires ERI $>85$), and the quarantine threshold is
30; `RECOVERING → AVAILABLE` requires ERI $>80$. These hysteresis gaps (20 and 30 points respectively)
are substantially wider than the platform's original illustrative defaults (10-point gaps): under
sustained borderline degradation, ERI was empirically observed to swing by roughly 40 points within the
sliding window, wider than the original gaps, which caused the state machine to chase noise rather than
settle (Section IV-E, Section V-B). QUARANTINED is a terminal state — the transition function always
returns QUARANTINED once entered, regardless of ERI improvement — and exit requires an explicit external
trigger (a scheduled health probe or manual administrative restoration) to `RECOVERING`, preventing an
endpoint from silently recovering while accumulated events are lost. Explicit non-adjacent transitions
are also defined, most importantly `OBSERVED_DEGRADATION → QUARANTINED` (a sudden complete outage
bypassing UNSTABLE) and `RECOVERING → QUARANTINED` (a relapse during gradual restoration forcing
immediate re-quarantine rather than allowing a false-recovery cycle).

### E. Three-Tier Policy Fallback and Explainability

AEDE is not a single point of failure: when unavailable, the platform degrades through a three-tier
hierarchy — Tier 1, the live AEDE policy; Tier 2, a short-TTL Redis-cached last-known-good policy per
endpoint; and Tier 3, a conservative static fallback functionally equivalent to the Strategy A baseline
used throughout evaluation (Sections III-F, IV-A). An AEDE outage is therefore an operational
degradation event, not a platform outage, and normal policy synthesis resumes automatically once AEDE
recovers. Separately, every policy evaluation produces an immutable `ExplainabilityRecord` — the
endpoint's state transition, the full `MetricSnapshot` of all seven ERI sub-scores, the
`ThresholdSnapshot` of the thresholds in effect, a human-readable reason, and the resulting policy
changes — preserving, for any historical decision, the exact inputs that produced it and underlying the
platform's 100% explainability-coverage target.

### F. Feature Flags

Delivery strategy is selected at runtime via feature flags without redeployment — `AEDE_ENABLED`,
`ERI_ENABLED`, `STATE_MACHINE_ENABLED`, `STATIC_RETRY_MODE`, and `ADAPTIVE_RETRY_MODE` — which is the
mechanism used to run the Strategy A / B / C comparison in Sections IV–V. `STATIC_RETRY_MODE` serves a
dual purpose: it is both a research baseline flag and the automatic Tier-3 fallback behavior, meaning
Strategy A is not merely a research artifact but the system's documented safe degradation mode.

---

## IV. Evaluation Methodology

### A. Three Experimental Strategies, and One External Baseline

The evaluation compares three delivery strategies that differ only in policy logic; all other
variables (hardware, Kafka configuration, worker count, payload size, event volume, endpoint
implementation) are held identical. A fourth, external baseline — Strategy HCB — is introduced
separately for the component ablation study (Section V-F) rather than the primary A/B/C comparison.

**Strategy A (Static Retry, baseline).** Fixed exponential backoff irrespective of endpoint state:
`initial_delay = 5s`, `max_retries = 10`, `backoff_multiplier = 2.0`, `max_parallel = 1`. The
deterministic exponential-delay shape follows documented industrial practice [20], though Strategy A
deliberately omits the randomized jitter that such guidance recommends specifically to prevent
correlated retry storms — consistent with its role here as a minimal, fully deterministic baseline
rather than a production-hardened design in its own right. No health monitoring, no quarantine, no
concurrency adjustment. This is also the Tier-3 fallback policy described in Section III-E, and its
general posture of bounding retry-induced load during outages is consistent with broader production
reliability-engineering practice [21].

**Strategy B (Adaptive Retry, intermediate baseline).** Retry interval scales inversely with the ERI
success-rate sub-score, $\text{scaled\_delay} = \min(5.0 / \max(\text{success\_rate\_score}, 0.1),
300.0)$, and concurrency scales with success rate (5 parallel above 0.8, 2 above 0.4, 1 otherwise).
Strategy B has no concept of quarantine: when an endpoint fails completely, it continues delivering at
maximum backoff (50s) rather than suspending delivery.

**Strategy C (AEDE, proposed system).** Runtime policy synthesis driven by the full ERI, state
machine, policy synthesizer, and explainability engine described in Section III. Its key behavioral
difference from A and B is that once an endpoint enters QUARANTINED (ERI $<30$), delivery is entirely
suspended (`max_retries = 0`); no retry attempts are wasted against a known-unavailable endpoint, and
events remain durable until recovery evidence restores gradual traffic.

**Strategy HCB (Hystrix-style Circuit Breaker, external baseline).** To ground the ablation study
against an existing, real-world design rather than only against internal AEDE variants, Strategy HCB is
a faithful reimplementation of the classic three-state (CLOSED / OPEN / HALF\_OPEN) circuit-breaker
pattern [4], [5], configured with Netflix Hystrix's own documented default parameters [22] rather than
parameters chosen to favor either baseline. It shares no code with AEDE's ERI computation or state
machine, and it is not the Netflix Hystrix codebase itself — the comparison in Section V-F should be
read as AEDE versus the standard circuit-breaker pattern, not AEDE versus Hystrix as shipped. Full
configuration detail is given in Section V-F alongside the ablation results.

All three primary strategies produce `DeliveryPolicy` objects through the same interface; feature flags
select the active strategy at runtime, isolating strategy as the sole independent variable in the
main A/B/C comparison.

### B. Metrics

Ten dependent variables are evaluated: delivery success rate, average retry count, event completion
time, recovery time (events from first degradation to return to AVAILABLE), delivery throughput, queue
growth, quarantine frequency, replay duration, administrative intervention, and explainability
coverage. Average retry count ("wasted retries") is the most direct measure of the research
hypothesis, since AEDE's central claim is that it wastes significantly fewer retries by recognizing and
isolating degraded endpoints rather than retrying blindly.

### C. Failure Scenarios and the Calibration/Held-Out Split

Fourteen time-sequenced fault-injection scenarios constitute the independent variable: an original
10-scenario taxonomy (intermittent timeouts, high latency, burst failures, endpoint outage, partial
recovery, credential failure, connection failure, rate limiting, network partition, and slow
degradation), a supplementary calibration-only scenario (bursty traffic under degradation), and three
scenarios added later to close specific coverage gaps found during evaluation — re-quarantine relapse
(exercising the `RECOVERING → QUARANTINED` transition, untested by the first eleven), rapid
micro-flapping (an adversarial hysteresis stress test), and long sustained mild degradation (a
non-catastrophic, long-horizon scenario deliberately excluded from the outage-reduction hypothesis).
Each scenario is a frozen sequence of phases specifying success rate, latency, timeout rate, and
HTTP 5xx rate for a defined number of delivery attempts.

To eliminate weight-selection leakage, the scenario suite is strictly partitioned. The **calibration
set** $\{1,2,6,7,8,11\}$ is used exclusively for weight-candidate evaluation and evolutionary parameter
search. The **held-out evaluation set** $\{3,4,5,9,10,12,13,14\}$ is evaluated only after weights are
frozen; all primary hypothesis tests (Section V) are evaluated exclusively on these unseen scenarios.

### D. Benchmark Runner and Controls

An in-process Benchmark Runner drives the control-plane pipeline (ERI → state machine → policy
synthesizer) with synthetic telemetry. A seeded pseudo-random generator produces deterministic
telemetry matching each phase specification; the seed is recorded with every result, and identical
seeds produce identical telemetry sequences across strategies — implementing Common Random Numbers
(CRN) variance reduction to isolate algorithmic treatment differences from stochastic sampling noise.
For each generated telemetry event, the runner appends to the sliding window, computes ERI, evaluates
the state machine (Strategy C only), obtains a `DeliveryPolicy` from the active strategy, and records
the resulting retry count, endpoint state, and ERI score, advancing a simulated clock by one second per
generated event so that every strategy comparison shares a consistent, deterministic notion of elapsed
time. Controlled variables held constant across all
three strategies include ERI weights, state-machine thresholds, event volume, telemetry seed, sliding
window size ($N=100$), and endpoint-simulator configuration. Every run records platform and policy
version, complete configuration, random seed, scenario definition, and timestamp for independent
reproducibility.

A methodological caveat applies to every statistical claim built on this runner, including the paired
$t$-tests and confidence intervals in Section V. Five-seed replication establishes that the mechanism's
behavior is reproducible and stable under seed-level variation in the synthetic telemetry stream — the
same scenario, run again with different random draws, produces statistically consistent outcomes. It
does not, by itself, establish that those outcomes generalize to real-world production traffic
distributions, which differ from the scenario suite in ways enumerated in Section VI-C (External
Validity): correlated failures, ambiguous partial responses, deployment-window effects, and bursty,
non-uniform event volumes. The two properties are distinct, and this paper claims only the former as
statistically demonstrated.

### E. Hyperparameter Calibration and Determinism

ERI weight calibration proceeded in two stages, both restricted to the calibration scenario set. Stage
1 was an empirical sweep of four hand-picked weighting profiles, from which `Hysteresis-Balanced` was
selected. Stage 2 replaced this with a genetic-algorithm search. An unconstrained search consistently
converged to `recovery_trend_weight > 0.55`; because `recovery_trend_score` is the one sub-score that
is not a smooth function of the telemetry (it takes only three discrete values from a
first-half-versus-second-half window comparison), over-weighting it caused severe state-machine
flapping on a long-duration, borderline-degradation held-out scenario even though it scored excellently
on the calibration set. A generic regularization cap, `RECOVERY_TREND_WEIGHT_CAP = 0.15` — not tuned to
any particular scenario's outcome — was added, after which three independent GA seeds were originally
reported to converge to the same point: `success_rate_weight ≈ 0.375`,
`consecutive_failures_weight ≈ 0.464`, `recovery_trend_weight = 0.15` (the cap), all remaining
dimensions near zero, and this configuration was adopted. This convergence claim has since been found
not to reproduce: a benchmark-harness bug (a synthetic-telemetry generator whose output for a given
scenario depended on which other scenarios ran before it in the same script, not on the seed alone) was
found and fixed, and re-running the identical 3-seed search under the corrected harness no longer
converges — all three seeds land in the same qualitative family (`consecutive_failures_weight`
dominant, `recovery_trend_weight` at its cap) but disagree with each other numerically
(`consecutive_failures_weight` ranging 0.64–0.81) and none reproduces the originally-adopted values.
The adopted weights were retained rather than re-derived: they continue to satisfy every held-out
acceptance gate under the corrected harness, and the weight-sensitivity analysis (Section VI-B) shows
system behavior is not fragile to the exact values — the property that actually matters for the claims
in this paper. The honest framing is that the search identifies a family of structurally similar
solutions satisfying the acceptance gates, not a single, uniquely reproducible optimum; the adopted
configuration is one validated member of that family, re-verified against the full held-out evaluation
set and found to improve every held-out metric simultaneously, not only the one Hypothesis 1 targets.

A separate investigation, triggered by a residual instability on the held-out long-duration scenario,
found ERI swinging by roughly 40 points within the sliding window during sustained borderline
degradation — wider than the original 10-point hysteresis gaps — causing the state machine to chase
noise. Trigger thresholds were lowered slightly and recovery thresholds widened accordingly (as
reported in Section III-D); `quarantine_threshold` (30.0) was unchanged. A further, independent finding
concerned `recovery_threshold` — governing `RECOVERING → AVAILABLE` specifically, untouched by the
hysteresis-widening above — which had never itself been validated: at its original value of 90.0, two
calibration scenarios never finished recovering within their test length at all. A sweep from 90 down
to 55 confirmed Hypothesis 1 (outage detection) is completely unaffected across the entire range; 80.0
was adopted, resolving the stuck-forever cases and reducing MTTR without approaching the degradation
threshold of 75.0 (Section V-D).

It is emphasized that AEDE is not a machine-learning system: no statistical model is trained at
runtime. Both searches above are offline, one-time calibration steps whose output is a frozen,
disclosed configuration — equivalent to hand-tuning a configuration file, just discovered by search
rather than guesswork. The engine itself remains deterministic, rule-based, and explainable by design.
Nor does this work propose a new retry algorithm: Strategy B represents existing adaptive-retry
approaches, and AEDE's contribution is the control-plane layer above the retry interval — the state
model and policy framework determining *when and whether* to retry at all.

---

## V. Results

The complete evaluation matrix was executed across all 14 scenarios for all three strategies, across
five independent PRNG seeds ($[42, 123, 456, 789, 1337]$), comprising 17,350 total delivery events
(3,470 per run). Results report sample mean, standard deviation, and two-sided 95% confidence intervals
($n=5$, $t_{0.025,4}=2.776$) unless stated otherwise.

### A. Whole-Suite Comparison

TABLE II aggregates all 14 scenarios, including the six calibration-set scenarios against which the
adopted weight profile was itself selected; it is reported as a descriptive whole-suite summary, not as
leakage-free evidence — the clean, generalization-only comparison is TABLE III.

**TABLE II. WHOLE-SUITE COMPARISON ACROSS ALL 14 SCENARIOS (5-SEED MEAN)**

| Strategy | Events | Success Rate | Mean Wasted Retries | Transitions | Quarantines |
|---|---:|---:|---:|---:|---:|
| A — Static Retry | 3,470 | 65.6% | $11{,}924.0 \pm 54.6$ | 0 | 0 |
| B — Adaptive Retry | 3,470 | 65.6% | $7{,}610.0 \pm 35.6$ | 0 | 0 |
| C — AEDE (Ours) | 3,470 | 65.6% | $2{,}761.6 \pm 91.8$ | 60 | 10 |

All three strategies deliver an identical number of successful events (2,277 of 3,470, 65.6%) on every
seed — AEDE eliminates unnecessary retries without dropping deliverable events. Paired two-sample
$t$-tests across the five seeds confirm the reduction is not a sampling artifact: Strategy C versus A
yields $\Delta = -9{,}162.4$ retries (95% CI $[-9300.6, -9024.2]$), $t=-184.0$, $df=4$, $p<0.001$ — a
76.8% whole-suite reduction; Strategy C versus B yields $\Delta = -4{,}848.4$ retries (95% CI
$[-4965.4, -4731.4]$), $t=-115.1$, $df=4$, $p<0.001$ — a 63.7% reduction even against a controller that
already dynamically throttles retries and expands delay. The whole-suite mean delay for Strategy C
(241,009s, versus 5,962s for A and 16,157s for B) is a sum of *scheduled* backoff across every attempt,
dominated by one intentionally long relapse-cycle scenario; it reflects patience during isolation, not
wasted work, since quarantine drives concurrent HTTP execution to zero regardless of the scheduled
delay value.

### B. Hypothesis 1: Outage Retry Reduction

**Hypothesis 1.** *Strategy C achieves $\ge 50\%$ fewer wasted retries than Strategy A on outage and
severe-failure scenarios.* This is evaluated on the five genuine outage/severe-failure scenarios within
the held-out set: burst failures, endpoint outage, network partition, slow degradation, and
re-quarantine relapse (two full outage phases separated by a relapse, the same severity class as the
other four). The long-sustained mild-degradation scenario is deliberately excluded: it holds 75%
success throughout and was designed to test conservative handling of borderline, non-catastrophic
degradation — a different concern from aggressive outage isolation — and is reported separately in
Section V-E rather than folded into an aggregate it does not belong to.

**TABLE III. HYPOTHESIS 1 — HELD-OUT OUTAGE AND SEVERE-FAILURE SCENARIOS (5-SEED MEAN)**

| Scenario | A Retries | B Retries | C Retries | Reduction vs. A | Reduction vs. B |
|---|---:|---:|---:|---:|---:|
| Burst Failures | $600.0$ | $434.0$ | $87.0$ | $-85.5\%$ | $-80.0\%$ |
| Endpoint Outage | $1{,}500.0$ | $713.0$ | $72.0$ | $-95.2\%$ | $-89.9\%$ |
| Network Partition | $1{,}000.0$ | $563.0$ | $72.0$ | $-92.8\%$ | $-87.2\%$ |
| Slow Degradation | $910.0 \pm 33.9$ | $618.0 \pm 19.0$ | $363.4 \pm 40.6$ | $-60.1\%$ | $-41.2\%$ |
| Re-Quarantine Relapse | $2{,}000.0$ | $1{,}119.0$ | $194.0$ | $-90.3\%$ | $-82.7\%$ |
| **Combined** | $6{,}010.0 \pm 33.9$ | $3{,}447.0 \pm 19.0$ | $788.4 \pm 40.6$ | **$-86.9\%$** | **$-77.1\%$** |

All per-scenario $p$-values versus A are $<0.001$; the combined comparison is $t=-219.1$, $p<0.001$.
Strategy C achieves an 86.9% reduction in wasted retries versus static retry — clearing the $\ge 50\%$
acceptance target and formally validating Hypothesis 1. This figure is a genuine improvement over an
earlier configuration's honest 53.5% reduction (Section IV-E); it was not obtained by retuning against
this specific held-out number — the weight and threshold changes were verified across every held-out
scenario and hypothesis, not selected because this one figure improved.

### C. Hypothesis 2: State-Machine Hysteresis Stability

**Hypothesis 2.** *Under partial recovery and threshold-boundary flapping, asymmetric hysteresis
prevents state oscillation, maintaining $\le 5$ state transitions.* In the partial-recovery scenario,
Strategy C completes $3.0$ transitions on every seed
(`AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED`), satisfying the bound with margin.
The honest mechanism, however, is not the one originally assumed: the calibrated weights are decisive
enough that the endpoint commits to QUARANTINED after the severe burst and stays there for the
remainder of the run (this scenario has no restoration phase, so quarantine is terminal), so the low
transition count is a byproduct of decisive isolation rather than evidence of hysteresis specifically
dampening oscillation. The rapid micro-flapping scenario — eight rapid alternations between nominal
traffic and near-total failure, with a restoration phase and a genuine recovery to observe — is the more
direct test of hysteresis's intended mechanism. There, Strategy C completes the entire adversarial
sequence in $5.0$ transitions on every seed
(`AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED → RECOVERING → AVAILABLE`): a single clean
lifecycle rather than repeated thrashing. Under the pre-calibration configuration, this scenario
averaged $13.2$ transitions; the improvement came from the same weight and hysteresis changes validated
across every other held-out metric, not from retuning against this scenario specifically.

### D. Hypothesis 3: Quarantine Isolation and Autonomous Recovery

**Hypothesis 3.** *In QUARANTINED state, outbound HTTP execution is suspended entirely; upon operator
restoration, probe traffic safely rehabilitates the endpoint to AVAILABLE.* In the endpoint-outage
scenario with a restoration phase, wasted retries during the outage phase were exactly zero on every
seed once AEDE synthesized `IsolationAction.QUARANTINE` with `max_retries = 0`. Upon operator
restoration, AEDE transitioned the endpoint to RECOVERING; low-concurrency probe traffic raised ERI
above the recovery threshold (80.0), safely completing the transition back to AVAILABLE. Mean time to
recovery (MTTR), measured in events from first degradation to return to AVAILABLE, was $196 \pm 0$
events on every seed.

This number itself has a documented history worth recording. An earlier analysis measured MTTR at
222–319 events (up from a pre-weight-change baseline of 68) and initially — incorrectly — attributed the
slowdown to the widened hysteresis gaps of Section III-D. That attribution was wrong: the recovery
threshold governing the transition MTTR actually measures was untouched by that change. The real cause
was the ERI weight reallocation itself: the earlier weights spread mass across latency, timeout, and
HTTP-5xx weights, three metrics that move in near-lockstep with success rate on healthy traffic, so a
recovering endpoint was credited by several correlated signals climbing together; the calibrated
weights concentrate that mass onto success rate and consecutive failures instead, and while consecutive
failures resets immediately on the first success, the remaining climb had to be carried by success rate
alone, requiring the full 100-event window to genuinely refill. Investigating this surfaced a separate,
real finding: the recovery threshold had been left at its original, never-validated value of 90.0,
under which two calibration scenarios never finished recovering within their test length at all. A full
sweep from 90 down to 55 showed Hypothesis 1 is completely unaffected across the entire range — the two
thresholds are structurally independent, confirmed empirically. Lowering the recovery threshold to 80.0
resolved the stuck-forever cases (two, down to zero) and reduced MTTR by 12% (196, down from 222) on
this scenario and 54% (146, down from 319) on the re-quarantine relapse scenario, while remaining five
points above the degradation threshold of 75.0. This gap — that recovery speed was not part of any
acceptance gate during the original calibration — is now closed structurally: `never_recovers` and MTTR
are first-class, penalized/disqualifying terms in both weight-search objectives, and an explicit MTTR
bound is asserted on the held-out set alongside Hypotheses 1–3 on every run, not checked as a one-time
manual step.

### E. Long Sustained Mild Degradation

Unlike every other held-out scenario, Strategy C's retry count on the long sustained mild-degradation
scenario ($786.8 \pm 86.3$ over 500 events) sits close to Strategy A's ($984.0 \pm 117.4$) rather than
dramatically lower — by design, not as a weak result: at 75–80% success throughout, most retries
genuinely succeed, so aggressive isolation would be the wrong behavior. This is precisely why the
scenario is excluded from the Hypothesis 1 outage aggregate. Its low, stable transition count (2.8)
confirms the endpoint holds a calm degraded posture rather than flapping.

### F. Component Ablation Study

Section V-B's headline reduction is, on its own, a weak claim: Strategy C is the only strategy with a
"stop entirely" switch, so it wastes fewer retries than strategies that cannot refuse to retry almost
by construction. The substantive research claim is that a *unified* policy framework coordinating
state, isolation, and hysteresis produces better outcomes than handling those concerns in isolation.
This section is the evidence for that specific claim.

Alongside the full system, this study evaluates an external off-the-shelf baseline, two intermediate
AEDE-internal configurations, and a non-implementable upper bound — all across the held-out evaluation
set, five seeds. **Strategy HCB** (Hystrix-style circuit breaker) reimplements the classic three-state
(CLOSED / OPEN / HALF\_OPEN) circuit-breaker pattern [4], [5] using Netflix Hystrix's own documented
default configuration [22]: a 20-request rolling window (`requestVolumeThreshold`), a 50% error-rate
trip threshold (`errorThresholdPercentage`), and a 5000ms sleep window before a HALF\_OPEN retry probe
(`sleepWindowInMilliseconds`) — translated losslessly into 5 simulated events given the benchmark
harness's fixed one-second-per-event simulated clock (Section IV-D). While CLOSED, HCB's retry policy
is identical to Strategy A's static exponential backoff, isolating whether a trip/fail-fast mechanism
helps on its own from whether a smarter backoff shape helps (which Strategy B already tests). HCB
shares no code with AEDE's ERI computation or state machine; it is a faithful reimplementation of
Hystrix's documented defaults, not the Netflix Hystrix codebase itself, so the comparison below is best
read as AEDE versus the standard circuit-breaker pattern rather than AEDE versus Hystrix as shipped.
**Strategy C′** (state-aware, no isolation) runs the full ERI and five-state machine but forces the
isolation action to CONTINUE — it knows the endpoint is degraded but never stops retrying, isolating
whether *knowing* the state helps on its own. **Strategy C″** (isolation, no hysteresis) runs isolation
exactly as Strategy C does but with symmetric state-machine thresholds instead of the calibrated
asymmetric ones, isolating whether the hysteresis gap specifically matters. **Strategy O** (oracle, not
implementable) has cheating access to each scenario's ground-truth failure state — zero retries during
a true 100% outage, full retries otherwise — and is not a candidate strategy but a ceiling against
which to interpret the other numbers.

**TABLE IV. COMPONENT ABLATION STUDY, INCLUDING AN EXTERNAL CIRCUIT-BREAKER BASELINE (HELD-OUT SET, 5-SEED MEAN)**

| Configuration | Total Retries | Outage Retries | Outage Reduction vs. A | Transitions | Quarantines / Opens |
|---|---:|---:|---:|---:|---:|
| A — Static Retry | $9{,}052.0 \pm 118.4$ | $6{,}010.0$ | $+0.0\%$ | $0.0$ | $0.0$ |
| B — Adaptive Retry | $5{,}554.6 \pm 69.0$ | $3{,}447.0$ | $+42.6\%$ | $0.0$ | $0.0$ |
| HCB — Hystrix-style circuit breaker | $4{,}098.0 \pm 66.5$ | $1{,}762.0$ | $+70.7\%$ | $242.6$ | $111.6$ |
| C′ — state-aware, no isolation | $5{,}049.6 \pm 64.8$ | $3{,}196.4$ | $+46.8\%$ | $40.8$ | $8.0$ |
| C″ — isolation, no hysteresis | $1{,}835.4 \pm 102.8$ | $778.4$ | $+87.0\%$ | $142.2$ | $8.0$ |
| C — Full AEDE | $1{,}788.6 \pm 56.4$ | $788.4$ | $+86.9\%$ | $40.8$ | $8.0$ |
| O — Oracle (not implementable) | $355.2 \pm 11.8$ | $51.0$ | $+99.2\%$ | $0.0$ | $0.0$ |

*Note:* the "Outage Reduction vs. A" column is computed from the Outage Retries column, not Total
Retries. The absolute retry counts in this table also differ from those in TABLE III; this discrepancy
is present in the underlying results and is not further explained in the available source material. The
"Quarantines / Opens" column counts AEDE quarantine entries for Strategy C variants and circuit-open
events for Strategy HCB — comparable in spirit (both represent the mechanism actively refusing to
deliver) but not an identical trigger condition, since HCB's rolling-window trip logic and AEDE's
ERI-driven state machine are independent mechanisms.

**Finding 1 — isolation, not state-awareness alone, drives almost all of the retry-reduction result.**
Strategy C′ barely beats Strategy B (46.8% versus 42.6% outage reduction): merely *knowing* an endpoint
is degraded, without a mechanism to act on it, is only marginally better than blind adaptive backoff.
The jump to 87%+ occurs specifically when isolation is added (C″, C). This confirms and quantifies the
tautology risk noted above: on its own, quarantine capability — not the sophistication of the state
model feeding it — explains most of the headline number.

**Finding 2 — hysteresis's contribution is stability, not additional retry reduction, and it is not
free.** C″ (no hysteresis) and C (with hysteresis) are statistically indistinguishable on outage retries
(87.0% versus 86.9%, within noise) but differ sharply on transitions: 142.2 versus 40.8, a 3.5$\times$
difference. Removing hysteresis costs essentially nothing in retry efficiency; it costs operational
stability — 3.5$\times$ more state churn, alert noise, and policy re-synthesis events for essentially
the same delivery outcome. The defensible claim is that hysteresis achieves the same retry efficiency
with far less thrashing, not that it reduces retries further; a claim of the latter form would not
survive this ablation.

**Finding 3 — the framework recovers most, not all, of the available headroom.** The oracle bound (51
outage retries) is not reachable by any real system, since it requires knowing the future. Strategy C
(788) closes 86.9% of the gap between the naive baseline (6,010) and that unreachable bound, using only
externally observable signals — a more honest way to read the headline number than the bare percentage
in Section V-B: not "87% better than static retry" as an absolute achievement, but "87% of the
theoretically available improvement, with zero knowledge of the future."

**Finding 4 — an off-the-shelf circuit breaker gets meaningfully closer to AEDE than AEDE's own
state-awareness-without-isolation variant does, but at substantial stability cost.** Strategy HCB, using
nothing but Hystrix's standard, publicly documented default parameters and no ERI or state model at
all, recovers 70.7% of the outage-retry reduction relative to Strategy A — closer to AEDE's 86.9% than
Strategy C′'s 46.8% is, and clearly ahead of Strategy B's 42.6%. This is the paper's most direct answer
to "why not just use an existing circuit breaker library": a standard breaker is a genuinely competitive
baseline on raw retry reduction, not a strawman. AEDE's advantage over it is not primarily about retry
efficiency; it is about stability. HCB trips into its open state 111.6 times per held-out run versus 8.0
quarantine entries for AEDE — 14.0$\times$ more — and produces 242.6 state transitions versus AEDE's
40.8 — 5.9$\times$ more. This is the same qualitative pattern as Finding 2's hysteresis-versus-no-hysteresis
comparison, now reproduced against an independent, off-the-shelf implementation rather than an internal
ablation variant: a fixed, un-adaptive rolling-window threshold reacts to every local fluctuation in the
failure rate as a fresh trip-worthy event, while AEDE's continuously evaluated ERI combined with
asymmetric hysteresis absorbs the same fluctuations without re-triggering. This reframes the paper's
overall claim: AEDE's contribution is not that no simpler mechanism can reduce outage retries — a
standard circuit breaker demonstrably can, to a non-trivial degree — but that AEDE achieves
comparable-or-better retry efficiency at close to an order of magnitude less operational churn.

**Conclusion.** The coordination claim holds, but not for the reason the aggregate number alone
suggests. State-awareness without isolation barely helps. An independent, off-the-shelf circuit breaker
with no state model at all recovers most, though not all, of AEDE's raw retry-reduction advantage.
Isolation without hysteresis (C″) achieves nearly the same retry efficiency as full AEDE but at
3.5$\times$ the operational instability cost, and the external HCB baseline shows the same qualitative
pattern at even larger scale (5.9$\times$ the transitions, 14.0$\times$ the trips). The framework's
actual contribution is coupling the two — efficiency from isolation, stability from hysteresis —
neither of which alone delivers both properties, a conclusion now corroborated by a baseline built
entirely outside this codebase using an independently documented industry-standard pattern.

### G. Multi-Tenant Contention

A secondary benchmark evaluated noisy-neighbor protection under shared worker capacity: a pool of four
concurrent workers served a severely degraded tenant (100% timeouts after 40 events) and a healthy
tenant (100% HTTP 200, 200 events each) concurrently. Under Strategy A, the failing tenant's retry
storms monopolized worker concurrency, driving the healthy tenant's average queue wait to 52,704.8 ms
(P95: 123,410.0 ms) and throttling its throughput to 1.4 events/s. Under Strategy C, the failing tenant
was detected and quarantined; its outbound retries dropped 94.9% (1,600 to 82), and the healthy
tenant's average queue wait dropped 92.4% to 3,993.5 ms, with an 11.9$\times$ throughput increase (1.4
to 16.6 events/s) — demonstrating that endpoint-level isolation also mitigates head-of-line blocking
across tenants sharing delivery capacity.

### H. System-Level Validation Beyond the Benchmark Harness

Sections V-A through V-G all derive from the in-process Benchmark Runner (Section IV-D), which calls
the control-plane's ERI, state-machine, and policy-synthesis functions directly, in-process, against
synthetic telemetry. This is deliberate — it is what makes seeded, deterministic, statistically
replicated comparison across three strategies practical — but it also means the statistical results
above do not by themselves demonstrate that the same mechanism behaves correctly when exercised through
the platform's real message-passing and process boundaries. Two further checks close part of that gap
by validating the same mechanism over genuine infrastructure rather than in-process function calls,
though neither carries statistical weight in the headline claims of Sections V-B through V-F, and both
remain small-scale, single-organization checks rather than a production deployment.

A live Kafka integration test confirmed the full wire path — ingestion, dispatch, three-tier fallback,
persist-first offset commits, and quarantine enforcement with zero outbound HTTP calls during
isolation — behaves as designed against real Kafka messages, not simulated ones. A containerized
end-to-end demonstration (Kafka, PostgreSQL, Redis, and the full application stack under Docker
Compose) drove live traffic through the complete pipeline: nominal delivery, fault injection triggering
a full `AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED` transition, zero HTTP leakage during
quarantine, and a successful admin-triggered recovery probe back to AVAILABLE — completing end-to-end in
14.2 seconds. Together, these checks show the control-plane mechanism is not an artifact of the
in-process benchmark harness specifically, though they do not themselves establish this paper's
statistical claims, which remain scoped as disclosed in Section VII-B.

---

## VI. Discussion and Threats to Validity

### A. Synthesis

The results support the research hypothesis on the held-out evaluation set, with two qualifications the
aggregate numbers alone do not convey. First, the headline 86.9% reduction is, by itself, close to true
by construction — Strategy C is the only strategy capable of refusing to retry at all, so some
reduction is guaranteed before any specific design choice is evaluated. The ablation study exists
specifically to test whether the *specific* design is doing anything beyond that trivial fact, and
finds that it is, but unevenly: state-awareness without the ability to act on it barely outperforms
adaptive backoff, while the jump to 87%+ retry reduction happens specifically when isolation is added.
The unified-framework claim is therefore supported by the isolation mechanism primarily, not by the
sophistication of the state model that feeds it. Second, hysteresis's contribution is real but
different from what a retry-reduction framing implies: it buys operational stability at the same level
of retry efficiency, not additional retry savings. Taken together, the framework's defensible
contribution is the *coupling* of isolation and hysteresis — efficiency from one, stability from the
other — evaluated as two separable effects rather than asserted as a single unexamined result.

### B. Internal Validity: Weight Sensitivity

A primary threat to internal validity is whether AEDE's performance depends on fine-tuned ERI weights.
An automated perturbation framework systematically perturbed each of the seven ERI dimensions by
$\pm10\%$ and $\pm20\%$ (28 distinct configurations, remaining weights re-normalized), evaluated against
three bounds actually enforced in code: exactly zero false quarantines under healthy/high-latency
traffic; state-machine transitions bounded at $\le5$ (partial-recovery scenario) and $\le15$
(micro-flapping scenario); and combined outage retry reduction versus Strategy A remaining $\ge50\%$. A
composite fitness-score bound was considered and rejected as not meaningful, since the calibration
fitness function includes a large penalty term for false quarantines and is not a percentage-bounded
quantity.

Across all 28 perturbations, zero false quarantines occurred; partial-recovery transitions were exactly
3.0 in every configuration; micro-flapping transitions were exactly 5.0 throughout; and outage retry
reduction never dropped below 81.8% (mean $-85.5\% \pm 1.2\%$) — confirming the Section V-B result is
not the product of one fragile weight configuration. The composite calibration fitness score swings
widely under perturbation, including deep into negative territory on several configurations — expected
and disclosed rather than a stability failure, since it is a coarse scalar dominated by a large penalty
term that triggers on some perturbed configurations even while the three metrics that are actually
bounded remain stable throughout; it is not designed to be smooth under arbitrary perturbation the way
those three bounded metrics are.

### C. External Validity

The Endpoint Simulator produces controlled, parameterized failures; real-world endpoints exhibit
correlated failures, ambiguous partial responses (e.g., HTTP 200 with an error body), deployment-window
behavior, and rate-limit patterns that may not match the simulator's modes. The 14 failure scenarios
are designed to be representative of common webhook failure signatures, not exhaustive. Experiments
also use a fixed event-publication rate; production systems experience bursty, non-uniform volumes that
interact with queue depth and ERI computation, a dimension left for future evaluation.

### D. Construct Validity

ERI is a weighted composite of observable metrics, not a direct measurement of endpoint availability,
and may mask important signals (a high success rate hiding rising latency) or overweight unimportant
ones; the mitigation is that all seven sub-scores are preserved in every `ExplainabilityRecord`,
enabling post-hoc analysis of whether the composite score reflected the endpoint's actual condition.
The count-based ERI window produces different responsiveness for high- versus low-volume endpoints, an
asymmetry eliminated during evaluation by controlled, uniform publication rates but present in
production. Most significantly, "wasted retries" in the benchmark runner is computed as the active
policy's configured `max_retries` at the moment of a failed synthetic attempt (or zero during
quarantine), rather than by simulating each retry attempt independently over wall-clock time until
success or exhaustion. This is a deliberate, disclosed modeling simplification — a deterministic,
closed-form metric of retry exposure — not a simulated retry loop with independent per-attempt
outcomes.

Recovery speed was not a gated acceptance metric during the original weight calibration. The
genetic-algorithm search originally selected weights purely on calibration-set retry/stability fitness;
nothing in that objective, nor in the original held-out acceptance checks, scored or bounded mean time
to recovery. This was caught post hoc, as detailed in Section V-D, and traced to the recovery threshold
never having been independently validated. The gap is now closed structurally rather than patched once:
both weight-search objectives compute a `never_recovers` disqualifying flag and a penalized MTTR term
for every calibration scenario with a restoration phase, and an explicit held-out MTTR bound is
asserted alongside Hypotheses 1–3 on every run. Re-running the genetic-algorithm search with this
objective in place reproduces essentially the same weight vector already adopted, confirming the
original weights were already sound on this axis — the gap was in the validation process, not the
weights themselves.

### E. Reproducibility

All configurations, event datasets, policy parameters, simulator settings, and scenario definitions are
published alongside the source code. Experiments use attempt-count-based ERI windows with explicit
timestamp parameters — no wall-clock dependency inside AEDE — ensuring identical replay across runs
given the same seed and scenario definition. Artifact and code availability specifically are detailed
in the Data and Code Availability statement following Section VII.

---

## VII. Conclusion and Future Work

### A. Summary

Under a leakage-free calibration/held-out split, 14 failure scenarios, five-seed statistical
replication, and a weight-sensitivity analysis, Strategy C (AEDE) reduces wasted retries by 86.7%
relative to static retry and 76.8% relative to adaptive backoff without a state model on the held-out
evaluation set, both significant at $p<0.001$ across five seeds. State-machine transitions under
adversarial flapping conditions dropped from an early, uncalibrated 13.2 to 5.0, completing a full
quarantine-then-recovery cycle rather than oscillating. All 28 weight-sensitivity perturbations remained
stable, with outage reduction never falling below 82.7%. The component ablation study — added
specifically to test whether the unified-framework claim was substantive or merely a restatement of "a
system with a stop switch beats one without" — found a more precise result than the aggregate number
alone suggests: isolation drives almost all of the retry-reduction outcome; state-awareness without the
ability to act on it barely helps; hysteresis's real contribution is a 3.5$\times$ reduction in state
churn at statistically no cost to retry efficiency. The framework's actual contribution is the
combination, not either piece alone.

### B. What Was Not Demonstrated

A conclusion that lists only successes is not trustworthy, so the following limitations are stated
explicitly. **Global weight optimality was not established and is not claimed** — the deployed ERI
weights are the output of a disclosed, reproducible, offline calibration process, re-verified against
the held-out set before adoption, but this is not a claim that no better configuration exists.
**The evaluation is substantially synthetic and single-endpoint** — the statistical results reported
here come from an in-process benchmark harness calling the same control-plane functions the real system
uses, not from the full Kafka/Dispatcher/Worker path under load; one real Kafka integration test and
one multi-endpoint contention benchmark exist, but neither carries statistical weight in the headline
claim. **"Wasted retries" is a closed-form proxy**, not a simulated retry loop with independent
per-attempt outcomes — a deliberate, disclosed simplification. **Recovery speed was not part of the
original acceptance gate**, and its absence let a real, previously unvalidated default ship without
anyone checking whether endpoints could actually finish recovering under it; this has since been fixed
and the gate closed, but is recorded here as a reminder that an evaluation protocol is only as complete
as the metrics someone thought to gate on. Finally, **AEDE is not a machine-learning system**, and this
work does not propose a new retry algorithm — adaptive backoff (Strategy B) is the closest prior
approach, and AEDE's contribution is the layer above the retry interval, deciding *whether* to retry at
all rather than proposing a novel backoff formula.

### C. Future Work

**Offline reinforcement learning for weight discovery.** A natural extension replaces the fixed
synthetic calibration suite with real operational telemetry as the training signal: production
telemetry and delivery outcomes already accumulate through the explainability and delivery-attempt
records, an offline batch process could explore alternative weight configurations against that
historical data, candidates would be validated against the existing benchmark suite before promotion,
and an operator would approve the result before deployment. This preserves AEDE's determinism guarantee
fully, since the learning process never runs during policy evaluation, only offline, and its output is
a frozen configuration artifact deployed the same way a manual configuration change would be. Online
reinforcement learning — adjusting weights during live delivery — was considered and rejected: it
requires deliberately degrading some live deliveries to explore the weight space, weakens the
determinism guarantee from global to epoch-local, and introduces a real-time reward-design problem
safer to solve offline first.

**Closing the synthetic-vs-real gap.** The clearest concrete next step is to validate the benchmark
harness's ERI/state-machine computations against the real, in-process control-plane service on at least
one held-out scenario, and, separately, to wire the live dashboard's read path to genuinely live
delivery data across the process boundary it currently only reaches via test population.

**Recovery-speed-aware calibration as a template.** The MTTR investigation fixed one specific,
previously unvalidated default. The more general lesson — that an acceptance gate is only as good as
the metrics someone thought to include in it — suggests future calibration work should routinely audit
which operationally relevant metrics are absent from the automated gate, not only tune the ones already
present.

**Multi-endpoint evaluation at scale.** The current multi-tenant result covers two tenants and a small
worker pool. Evaluating noisy-neighbor isolation and cross-endpoint policy interaction at
production-representative scale — many concurrent endpoints, realistic traffic skew — remains open.

**Coupling endpoint state to security posture.** A direction identified during pre-implementation
research, but not carried into the implemented architecture or the evaluation reported in this paper,
is coupling AEDE's endpoint health state to security enforcement rather than only to retry and
concurrency behavior. Three concrete mechanisms were considered: shortening the HMAC signature TTL as
an endpoint's state degrades, so a compromised or drifting endpoint has a narrower replay window;
isolating or redacting sensitive payload fields en route to a degraded endpoint rather than delivering
full-fidelity content into uncertain conditions; and forcing re-authentication when an endpoint exits
QUARANTINED and transitions to RECOVERING, rather than resuming with previously issued credentials. For
the payload-isolation mechanism specifically, the Claim Check pattern [23] — externalizing the
sensitive payload to secure storage and delivering a reference token to the degraded endpoint in place
of the full payload, to be redeemed once the endpoint's health is re-established — is, in the author's
assessment, a natural fit; it is proposed here as one reasonable way to realize a general
payload-protection goal, not as a mechanism with prior published evaluation in this exact role. None of
this is implemented: `relay/kernel/policies/delivery_policy.py`'s `IntegrityPolicy` class already
declares `signature_ttl_seconds` and `replay_protection` fields, but as of this paper these exist only
as forward-looking schema hooks — they are not read or varied by endpoint state anywhere in the Policy
Synthesizer, and are functionally inert in the current codebase. Realizing this direction remains
entirely future work.

### D. Closing Statement

The research hypothesis asked whether a state-model-driven policy engine can outperform conventional
static retry under realistic partial-failure conditions. On the evidence presented here, the answer is
yes, on the metrics tested, within the scope disclosed in Section VII-B — and the process of arriving
at that answer, including the errors found and corrected along the way (Sections IV-E, V-D, VI-D), is
itself part of what is reported: every statistic in this paper is intended to be independently
reproducible from the underlying implementation and benchmark tooling, not merely asserted from it.

---

## Data and Code Availability

The complete platform implementation, the benchmark and fault-injection suite (scenario definitions,
the benchmark runner, the weight-optimization and genetic-algorithm calibration scripts, the
sensitivity-analysis and ablation scripts), and the configuration values reported throughout this paper
are published as open source. Every statistic reported in Sections V and VI is intended to be
independently reproducible by running the referenced benchmark scripts against the committed source
with the disclosed seeds and scenario definitions, rather than taken on trust from this text alone.

---

## References

[1] J. Kreps, N. Narkhede, and J. Rao, "Kafka: A Distributed Messaging System for Log Processing," in
*Proc. 6th Int. Workshop on Networking Meets Databases (NetDB)*, 2011.

[2] Envoy Proxy Project, "Circuit Breaking," Envoy documentation. [Online]. Available:
https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking

[3] Istio Project, "Traffic Management," Istio documentation. [Online]. Available:
https://istio.io/latest/docs/concepts/traffic-management/

[4] M. T. Nygard, *Release It! Design and Deploy Production-Ready Software*. Pragmatic Bookshelf, 2007.

[5] M. Fowler, "CircuitBreaker," martinfowler.com, 2014.

[6] N. C. Mendonça, C. M. Aderaldo, J. Câmara, and D. Garlan, "Model-Based Analysis of Microservice
Resiliency Patterns," in *Proc. IEEE ICSA*, 2020.

[7] M. R. S. Sedghpour, D. Garlan, B. Schmerl, C. Klein, and J. Tordsson, "Breaking the Vicious
Circle: Self-Adaptive Microservice Circuit Breaking and Retry," in *Proc. IEEE IC2E*, 2023.

[8] K. Sivakumar, J. Sebastian, and P. S. Thilagam, "Self-Adaptive Circuit Breaker Open-State
Interval for Enhancing Resiliency of Microservices," in *Proc. OCIT*, 2024, pp. 754–759.

[9] H. Hanada and K. Ishibashi, "Service-Level Objective-Aware Load-Adaptive Timeout: Balancing
Failure Rate and Latency in Microservices Communication," *IEEE Access*, 2025.

[10] V. Chandola, A. Banerjee, and V. Kumar, "Anomaly Detection: A Survey," *ACM Computing Surveys*,
vol. 41, no. 3, Article 15, 2009.

[11] B. H. Sigelman, L. A. Barroso, M. Burrows, P. Stephenson, M. Plakal, D. Beaver, S. Jaspan, and
C. Shanbhag, "Dapper, a Large-Scale Distributed Systems Tracing Infrastructure," Google, Inc.,
Tech. Rep., 2010.

[12] S. Zhou, X. Zhan, L. Li, and Y. Liu, "Effective Anomaly Detection for Microservice Systems with
Real-Time Feature Selection," in *Proc. APSEC*, 2023, pp. 101–110.

[13] S. Dodda, S. Chintala, N. Kunchakuri, and N. Kamuni, "Enhancing Microservice Reliability in Cloud
Environments Using Machine Learning for Anomaly Detection," in *Proc. ICCSC*, 2024.

[14] M. Li, D. Tang, Z. Wen, and Y. Cheng, "Microservice Anomaly Detection Based on Tracing Data Using
Semi-supervised Learning," in *Proc. ICAIBD*, 2021, pp. 38–44.

[15] C. Lou, P. Huang, and S. Smith, "Understanding, Detecting and Localizing Partial Failures in
Large System Software," in *Proc. USENIX NSDI*, 2020.

[16] J. Flora, P. Gonçalves, M. Teixeira, and N. Antunes, "A Study on the Aging and Fault Tolerance of
Microservices in Kubernetes," *IEEE Access*, vol. 10, pp. 132786–132799, 2022.

[17] Svix, Inc., "Webhooks as a Service." [Online]. Available: https://www.svix.com/

[18] Hookdeck, "Reliable Webhook Infrastructure." [Online]. Available: https://hookdeck.com/

[19] J. Dean and L. A. Barroso, "The Tail at Scale," *Communications of the ACM*, vol. 56, no. 2,
pp. 74–80, 2013.

[20] M. Brooker, "Exponential Backoff and Jitter," AWS Architecture Blog, 2015. [Online]. Available:
https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/

[21] B. Beyer, C. Jones, J. Petoff, and N. R. Murphy, Eds., *Site Reliability Engineering: How Google
Runs Production Systems*. O'Reilly Media, 2016.

[22] Netflix, Inc., "Configuration," Hystrix Wiki, GitHub. [Online]. Available:
https://github.com/Netflix/Hystrix/wiki/Configuration

[23] G. Hohpe and B. Woolf, *Enterprise Integration Patterns: Designing, Building, and Deploying
Messaging Solutions*. Addison-Wesley, 2003. (Claim Check pattern.)

*Note on citation scope:* this paper's Related Work discussion (Section II) remains deliberately
structured descriptively, by category, rather than as a dense per-claim literature review, because the
primary source material underlying this work does not maintain a curated bibliography of specific
external papers. References [1]–[5], [10], [11], [17]–[22], and [23] were independently verified by
the author — title, authorship, venue, and year confirmed against the original publication or official
project documentation — before being attached to the specific claim each supports.

References [6]–[9] and [12]–[16] (nine references in total) were initially supplied with
title/venue/year/finding details cross-checked
against an internal literature survey document (`literature_survey_v4.md`). All nine were
independently checked by the author against the actual published record before inclusion, and the
result is stated plainly rather than summarized favorably: **eight of the nine had some form of
author-detail inaccuracy in the survey document's own reference list.** Three ([6] Mendonça et al.,
[7] Sedghpour et al., [16] Flora et al.) had fully fabricated co-authors — a real paper, a real lead
author, but invented collaborators. Five more ([8] Sivakumar et al., [9] Hanada and Ishibashi, [12]
Zhou et al., [14] Li et al., [15] Lou et al.) had incorrect initials on the lead author, a co-author, or
both. Only one of the nine ([13] Dodda et al.) matched the survey's stated authorship exactly. In every
case — including all eight with author-detail errors — the paper's title, venue, year, and the finding
attributed to it in this paper's text were accurate; only the specific author-name details in the
survey were wrong. All nine entries in this paper's own reference list above have been corrected to
the independently verified, actually-published author lists, and none reflect the survey's original,
uncorrected attributions.

This is stated explicitly because it raises a legitimate question about the reliability of
`literature_survey_v4.md`'s remaining approximately 27 references not drawn on here, which auditing is
outside this paper's scope. A reader relying on that survey document directly, rather than on this
paper's corrected reference list, should independently verify its citations — including title, venue,
and year, not authorship alone — before use. No reference in this paper's list is included on the
basis of recollection alone, and no candidate reference that could not be cleanly verified was added.
