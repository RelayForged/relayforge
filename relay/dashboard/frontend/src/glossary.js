// Plain-language explanations behind every "?" in the dashboard.
// Written for someone who has never seen the project: what it is, and why
// it matters. Keep each one short enough to read in ten seconds.

export const GLOSSARY = {
  endpoint_state: {
    title: "Endpoint state",
    body:
      "Relay sorts every customer endpoint into one of five health states. The state decides how carefully Relay sends to it: normally when AVAILABLE, more gently when degraded or unstable, not at all when QUARANTINED.",
  },
  states: {
    title: "The five states",
    body:
      "AVAILABLE: healthy. OBSERVED_DEGRADATION: some problems, sending is slowed. UNSTABLE: frequent failures, one request at a time with long gaps. QUARANTINED: failing badly, Relay stops sending and holds the events. RECOVERING: back after a fix, on probation until it proves itself.",
  },
  eri: {
    title: "Endpoint Reliability Index (ERI)",
    body:
      "A health score from 0 to 100, recalculated after every delivery from seven signals: success rate, latency, failure streaks, timeouts, server errors, queue depth and recovery trend. High is healthy. The score is what moves the endpoint between states.",
  },
  live_stream: {
    title: "Live stream",
    body:
      "The dashboard receives a fresh snapshot every second over a WebSocket. \"live\" means it reflects real deliveries on the running system; \"synthetic\" would mean generated demo data.",
  },
  fault_injection: {
    title: "Fault injection",
    body:
      "The webhook receiver here is a simulator we control. These settings make it fail on purpose (errors, timeouts, slowness) so you can watch Relay react. In real life this would be a customer's server having a bad day.",
  },
  scenario_presets: {
    title: "Scenario presets",
    body:
      "One-click fault profiles that tell a story, from healthy to a full outage and back. Each sets the simulator and keeps a steady stream of events flowing so the effect is visible.",
  },
  aede: {
    title: "AEDE, the policy engine",
    body:
      "The Adaptive Event Delivery Engine is Relay's brain. It watches each endpoint's health and decides how to deliver to it: how many requests at once, how long to wait between retries, and when to stop. The workers just follow its decisions.",
  },
  aede_outage: {
    title: "Simulated AEDE outage",
    body:
      "What if the brain itself fails? Delivery must not stop. Relay first keeps using the last decision AEDE made (Tier 2, cached policy), and once that expires falls back to safe fixed rules (Tier 3). Bring AEDE back and it takes over again.",
  },
  eri_history: {
    title: "ERI history",
    body:
      "The health score over time. Coloured bands are the state zones; the dashed lines are the thresholds where the state changes. Recovery needs a higher score than degradation (hysteresis), so an endpoint hovering near a line does not flip back and forth.",
  },
  hysteresis: {
    title: "Hysteresis",
    body:
      "Different thresholds for getting worse and getting better. An endpoint degrades below 75 but only counts as healthy again above 95. This stops it flapping between states on small wobbles.",
  },
  sub_metrics: {
    title: "ERI factors",
    body:
      "The seven signals behind the score, each shown as a health value where the outer edge is perfect. When the shape caves in on one axis, that is the reason the score dropped: for example, timeouts during an outage.",
  },
  sparklines: {
    title: "Factor scores over time",
    body:
      "Three of the seven factors, tracked over time as health scores (100% = no problems). They show which signal started dropping first.",
  },
  delivery_timeline: {
    title: "Delivery timeline",
    body:
      "Every attempt to deliver an event to the endpoint, newest first: whether it worked, the HTTP response, and which attempt it was. Retries of the same event appear as attempt #2, #3 and so on.",
  },
  signature: {
    title: "Signed requests",
    body:
      "Every request carries a cryptographic signature (HMAC-SHA256) made with a shared secret. The receiver recomputes it to prove the request really came from Relay and was not altered. \"verified\" means the receiver checked it.",
  },
  policy_decision: {
    title: "Policy decision",
    body:
      "The delivery rules AEDE chose for this endpoint right now, based on its state. When the endpoint gets sicker, these get more cautious; when it heals, they relax.",
  },
  max_parallel: {
    title: "Max parallelism",
    body:
      "How many deliveries to this endpoint may be in flight at the same time. Lowered when the endpoint struggles, so Relay does not pile on.",
  },
  queue_priority: {
    title: "Queue priority",
    body: "How urgently this endpoint's work is scheduled relative to others. Healthy endpoints go first.",
  },
  throughput_limit: {
    title: "Throughput limit",
    body: "An optional cap on deliveries per second to this endpoint. Empty means no cap.",
  },
  max_retries: {
    title: "Max retries",
    body:
      "How many times a failed delivery is tried again before it is given up and moved to the dead letter queue for later replay.",
  },
  backoff: {
    title: "Backoff",
    body:
      "How the wait between retries grows. Exponential means each wait is longer than the last (for example 15 s, 30 s, 60 s), giving a struggling server room to recover.",
  },
  retry_delay: {
    title: "Retry delay",
    body: "The wait before the first retry, and the longest any single wait may grow to.",
  },
  isolation: {
    title: "Isolation",
    body:
      "What Relay does with this endpoint as a whole. CONTINUE: normal. REDUCE_CONCURRENCY: fewer at once. PAUSE: one at a time with long gaps. QUARANTINE: stop sending entirely and hold events until it is restored.",
  },
  explainability: {
    title: "Policy explainability",
    body:
      "Every decision AEDE makes is recorded with its reason: the score, which threshold was crossed, and what changed. Nothing the system does to an endpoint is a black box.",
  },
  window_size: {
    title: "Window size",
    body:
      "How many recent deliveries the score is calculated from. A sliding window means old history fades out and the score reflects the endpoint's current behaviour.",
  },
  dlq: {
    title: "Dead letter queue (DLQ)",
    body:
      "Events that could not be delivered: retries ran out, or the endpoint was quarantined. Nothing is thrown away. Once the endpoint is healthy, Replay sends them again as new attempts; the original failed records are kept unchanged for the audit trail.",
  },
  dlq_reason: {
    title: "Why it is here",
    body:
      "FAILED: every retry was used up. DEAD_LETTER: delivery was stopped, for example by quarantine mid-retry. PARKED: the endpoint was already quarantined, so it was never sent at all.",
  },
  journal: {
    title: "State transition journal",
    body:
      "Every time the endpoint changed state, in order, with the score and the rule that triggered it. The whole story of an incident on one screen.",
  },
  restore: {
    title: "Restore endpoint",
    body:
      "An operator's \"I fixed it\" button. It moves a quarantined endpoint to RECOVERING, where it must deliver successfully for a while before it is fully trusted again. Held events are not resent automatically; use Replay in the dead letter queue.",
  },
  strategies: {
    title: "Strategies A, B and C",
    body:
      "A: the industry default, fixed retries with exponential backoff, the same for every endpoint. B: smarter backoff that adapts to failures, but with no idea of endpoint health. C: AEDE, which tracks each endpoint's health and changes how it delivers, up to quarantining it.",
  },
  benchmark: {
    title: "The benchmark",
    body:
      "The same failure scenarios played against strategies A, B and C in a deterministic simulation, so the only difference is the strategy. The AEDE decisions come from the real engine code. It runs in under a second because it is simulated, not real time; use the Scenario Lab to watch one play out.",
  },
  held_out: {
    title: "Held-out scenarios",
    body:
      "There are 14 scenarios. Six were used to tune the ERI weights; the other eight were kept aside and only used to evaluate. Results are reported on those eight, because scoring on the scenarios we tuned on would flatter the engine.",
  },
  wasted_retries: {
    title: "Wasted retries",
    body:
      "Retries sent to an endpoint that could not succeed at the time. Each one costs worker time and network traffic and hammers a server that is already down. Fewer is better.",
  },
  success_rate: {
    title: "Success rate",
    body:
      "The share of events eventually delivered. AEDE does not deliver more successfully than the others; its win is getting the same result with far less wasted effort.",
  },
  multi_endpoint: {
    title: "Why compare with a healthy neighbour?",
    body:
      "Endpoints share one pool of delivery workers. A dead endpoint with naive retries keeps those workers busy on requests that will time out, so a perfectly healthy customer's events wait in line behind it. The neighbour is the victim. The test: does the healthy endpoint keep its speed while the other one fails?",
  },
  policy_tier: {
    title: "Policy tiers",
    body:
      "Tier 1: a live decision from AEDE. Tier 2: AEDE is unreachable, so the last decision it made is reused for a short time. Tier 3: that has expired too, so fixed, safe default rules apply. Delivery continues at every tier.",
  },
};

// What each live scenario preset simulates and what to watch for.
export const SCENARIO_GUIDE = {
  normal: {
    story: "The customer's server is healthy: every webhook is accepted straight away.",
    watch: "Score stays near 100, state AVAILABLE, a steady stream of green deliveries.",
  },
  early_degradation: {
    story: "The server starts struggling: 40% of requests fail or respond slowly.",
    watch: "The score drifts into the yellow band and AEDE eases off by sending fewer requests at once.",
  },
  instability: {
    story: "Half of all requests now fail with a server error.",
    watch: "State UNSTABLE: AEDE sends one request at a time with long gaps between retries.",
  },
  full_outage: {
    story: "The server stops answering; every request times out.",
    watch: "The score collapses and the endpoint is QUARANTINED: Relay stops sending and holds the events in the dead letter queue.",
  },
  recovery: {
    story: "The server is fixed and an operator brings the endpoint back.",
    watch: "RECOVERING for about ten seconds while it proves itself, then AVAILABLE. Replay the held events afterwards.",
  },
  rate_limiting: {
    story: "The server is overloaded and answers most requests with 'too many requests' (HTTP 429).",
    watch: "The score drops on the success factor while latency stays fine: a different fingerprint on the radar.",
  },
  auth_failure: {
    story: "The customer rotated their credentials; every request is rejected with HTTP 401.",
    watch: "Fast, total failure: the endpoint heads to quarantine instead of being retried forever.",
  },
  network_partition: {
    story: "The network to the server is cut: connections are dropped with no response at all.",
    watch: "Failures with no HTTP status; the endpoint is isolated just like a full outage.",
  },
};
