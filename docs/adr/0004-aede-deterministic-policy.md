# ADR 0004: Use Deterministic Rule-Based Policy Evaluation in AEDE (v1)

## Status

Accepted

## Context

The Adaptive Event Delivery Engine (AEDE) must evaluate endpoint state and synthesize Delivery Policies. The central design question is: what kind of policy engine should drive these decisions?

Two broad approaches are available:

**Option A — Machine Learning / Statistical Models**: Train a model on historical delivery data to predict endpoint failures, classify endpoint health, or optimize retry schedules. Techniques could include anomaly detection (e.g., Isolation Forest, LSTM autoencoders), reinforcement learning for backoff optimization, or Bayesian classifiers for state estimation.

**Option B — Deterministic Rule-Based Engine**: Define explicit thresholds, weighted scoring, and state machine transition rules. Given identical inputs, the engine always produces identical outputs.

The research contribution of this project is not to propose a new ML model. The research contribution is the **Adaptive Event Delivery Policy Framework**: a unified runtime system that connects endpoint state, delivery policy, and developer observability. The evaluation methodology compares delivery strategies under controlled failure conditions.

This comparison requires the following properties from the policy engine:

1. **Reproducibility**: Running the same experiment twice must produce the same decisions. ML models with stochastic elements, online learning, or time-dependent state cannot guarantee this.

2. **Explainability**: Every policy decision must expose exactly why it was made — which metrics, which thresholds, which rules triggered. Neural network and ensemble models cannot provide this natively.

3. **Auditability**: Developers must be able to inspect any historical policy decision and understand it without running the model again. A rule-based engine stores decisions as human-readable records; a trained model requires the model weights, input features, and inference pipeline to reproduce the same output.

4. **Experimental control**: The evaluation compares three strategies. The policy engine must be a controlled, understood variable — not an adaptive black box that changes behavior between experiment runs.

ML-based approaches may be valuable for future research extensions (anomaly detection, predictive failure classification, congestion control). The architecture intentionally isolates AEDE behind a clean interface to support these extensions without changing the Data Plane.

## Decision

Implement AEDE v1 using a **deterministic, rule-based, weighted-scoring policy engine**.

The engine operates as follows:

1. **Collect telemetry** — externally observable signals (success rate, latency, timeouts, consecutive failures, etc.)
2. **Compute ERI** — weighted combination of normalized metrics, producing a score from 0 to 100.
3. **Evaluate state machine** — compare ERI against configurable thresholds with hysteresis to determine endpoint state.
4. **Synthesize Delivery Policy** — select policies corresponding to current endpoint state and produce an immutable DeliveryPolicy object.
5. **Record explainability** — persist the metrics, thresholds crossed, state transition, and applied policies as an immutable ExplainabilityRecord.

All thresholds and weights are externalized in configuration. This allows experimental evaluation of different threshold configurations without code changes.

### Rejected Alternatives

**Reinforcement Learning**: An RL agent could learn optimal retry schedules through interaction with the environment. However, RL agents require many episodes to converge, may never produce identical decisions across runs (stochastic exploration), and cannot explain individual decisions without significant additional instrumentation. Rejected for v1; noted as a future research direction.

**Anomaly Detection Models (statistical or ML)**: Could classify endpoint behavior more accurately than threshold-based rules under noisy or ambiguous signals. However, model training introduces data dependencies, model version management, and reproducibility concerns. Rejected for v1.

**Bayesian Policy Optimization**: Principled probabilistic approach to adapting retry schedules. Requires prior distributions and produces probabilistic outputs rather than deterministic decisions. Rejected for v1 due to reproducibility requirements.

## Consequences

**Positive:**
- Given identical inputs, AEDE always produces identical outputs. Experiments are reproducible.
- Every decision is fully explainable: the ERI score, individual metric contributions, threshold values, and selected policies are all stored and queryable.
- No training data required. The engine operates from first delivery.
- Threshold and weight configuration allows experimental comparison without code changes.
- Simpler to implement, test, and audit than any ML-based alternative.
- Future ML extensions can be introduced by implementing a new policy engine behind the same AEDE interface.

**Negative:**
- Rule-based systems may be less adaptive than ML models under novel or complex failure patterns not anticipated during design.
- Threshold tuning requires domain knowledge and experimental iteration.
- The engine cannot automatically improve from accumulated delivery history (in v1).

**Accepted trade-offs:**
- The loss of adaptive accuracy is acceptable in v1 because the primary research contribution is the policy framework architecture and its comparative evaluation against baselines — not the sophistication of the classification algorithm within it.
- Future research can replace the rule-based engine with an ML model behind the same interface, enabling direct comparison of the framework with and without ML-based decision making.

The deterministic rule-based engine is not a limitation of ambition — it is a deliberate design choice that makes the research contribution reproducible, the decisions explainable, and the experimental comparison valid.
