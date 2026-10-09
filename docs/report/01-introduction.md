# Chapter 1 — Introduction & Problem Statement

## 1.1 Motivation

Modern software systems rely on webhooks to notify external systems of business events — an order
placed, a payment settled, a subscription renewed. Despite appearing simple, webhook delivery crosses
organisational and infrastructural boundaries the sender cannot control: network latency, endpoint
availability, server configuration, rate limits, deployment schedules, maintenance windows. Failures
are therefore **expected, not exceptional**.

Yet webhook delivery is typically implemented as a simple HTTP request followed by a retry loop:

```python
try:
    requests.post(url, data=payload)
except:
    retry()
```

This approach fails in production. As applications scale, developers independently rebuild the same
supporting infrastructure — retry queues, replay mechanisms, dead letter queues, delivery dashboards,
monitoring, alerting — re-implemented inconsistently across teams, with limited reliability
guarantees and no shared understanding of *why* a given retry policy behaved the way it did.

Applications should not be responsible for managing event delivery. Reliable outbound event delivery
should be treated as infrastructure — just as developers do not build their own databases, they should
not repeatedly implement reliable delivery systems.

## 1.2 The Core Problem: Webhook Delivery as a Distributed Systems Problem

Relay reframes the problem: instead of centring the system on HTTP requests, it centres on immutable
events flowing through a durable pipeline. Applications publish business events; the platform owns
everything after that — scheduling, delivery, retry, replay, failure isolation, monitoring, and
observability (Chapter 3).

The specific gap this work addresses is narrower than "build a reliable webhook platform." Reliable
delivery infrastructure — durable queues, retry scheduling, replay, dead-letter handling — is a solved
engineering problem; Kafka, and commercial webhook platforms built on similar primitives, already do
this well. What remains largely unaddressed, in the published literature specifically, is the *policy*
question sitting on top of that infrastructure: **given only externally observable signals about an
endpoint's behaviour, what should the delivery policy be at this moment, and can that decision be made
in a way that is deterministic, explainable, and demonstrably better than the conventional
alternatives?** Chapter 2 positions this gap against five adjacent research domains; Chapter 3 describes
the system built to close it; Chapters 4 and 5 are the experimental evidence for whether it actually does.

## 1.3 Research Question and Hypothesis

**Research Question:** Can adaptive runtime delivery policies driven by continuously evaluated endpoint
state outperform conventional static retry mechanisms in webhook-based event delivery systems, under
realistic partial failure conditions?

**Research Hypothesis:** Runtime delivery policies derived from continuously evaluated endpoint state
can improve delivery efficiency, reduce unnecessary retries, decrease administrative intervention, and
increase system resilience compared with conventional static retry strategies, while maintaining
complete transparency and reproducibility.

This is deliberately a *systems/architecture* hypothesis, not a claim about any particular numeric
configuration. The object of study is whether coupling continuous endpoint-state evaluation to policy
synthesis — the architecture — improves delivery outcomes, not whether any specific set of weights or
thresholds is optimal. Chapter 4 §4.9 discloses exactly how the specific configuration used in this
evaluation was calibrated, and is explicit that it is not claimed to be a global optimum.

## 1.4 The Proposed System: AEDE

The **Adaptive Event Delivery Engine (AEDE)** is the control-plane component that answers this
question empirically. It derives an **Endpoint Reliability Index (ERI)** — a deterministic composite
score computed only from externally observable signals (HTTP status codes, latency, timeouts,
connection failures) — drives a five-state endpoint state machine with hysteresis, and synthesises an
immutable `DeliveryPolicy` object that a stateless data plane executes without making any runtime
decisions of its own. Every synthesised policy is accompanied by an `ExplainabilityRecord`: the metrics,
thresholds, and reasoning that produced it.

AEDE is deliberately **not** a machine learning system. No model is trained; no parameters are learned
from live traffic. Given identical telemetry, AEDE always produces identical policies — a design choice
made explicitly to keep the system's behaviour predictable, auditable, and testable (Chapter 3 §3.2;
ADR 0004). Where a genetic-algorithm search is used in this work, it is used offline, once, to calibrate
a small number of disclosed hyperparameters (the ERI weights) — not as a runtime learning mechanism.
Chapter 4 §4.9 is explicit about this distinction and about the search process's own limitations.

## 1.5 Contributions

This work makes four contributions, detailed and evidenced in Chapter 4 and summarised in Chapter 7:

1. A state-driven model of endpoint health derived entirely from externally observable signals — no
   access to, or cooperation from, the subscriber's infrastructure is assumed or required.
2. A policy framework that coordinates retry scheduling, endpoint isolation, and hysteresis-based state
   stability through a single engine, rather than three independently-tuned mechanisms — evaluated
   directly via a component ablation study (Chapter 5 §5.8), which shows *which specific piece* is
   responsible for which specific outcome rather than asserting coordination is beneficial without
   evidence.
3. A reproducible, open-source implementation of the full platform, including infrastructure,
   configuration, and benchmark tooling.
4. A controlled fault-injection and evaluation framework — 14 scenarios, a leakage-free
   calibration/held-out split, multi-seed statistical testing, weight sensitivity analysis, and an
   automated acceptance gate — reusable for future research into adaptive event delivery policies.

## 1.6 Report Structure

- **Chapter 2** positions this work against five adjacent research domains and states the specific
  literature gap it addresses.
- **Chapter 3** describes Relay's architecture: the Control Plane / Data Plane separation, AEDE's
  internal components, the domain model, and the platform's reliability guarantees.
- **Chapter 4** describes the experimental methodology: strategies, metrics, scenarios, controls, and
  hyperparameter calibration.
- **Chapter 5** presents the empirical results: statistical hypothesis testing and the per-scenario
  breakdown, including the component ablation study that substantiates Contribution 2 directly.
- **Chapter 6** discusses the results and their threats to validity: weight sensitivity, external
  validity, and construct validity.
- **Chapter 7** summarises what was and was not demonstrated, and states future work.
