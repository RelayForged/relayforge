import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { DASHBOARD_URL } from "./config";
import HelpTip from "./HelpTip";

// Replays one benchmark scenario event by event under Strategies A, B and C.
// The numbers are the benchmark's own (GET /api/chart/scenarios/{id}/trace);
// only the pacing is added, so a sub-second simulation can be watched.

const STRATEGIES = [
  {
    key: "STRATEGY_A",
    short: "A",
    name: "Fixed retries",
    color: "#dc2626",
    blurb: "The industry default: every failure is retried up to 5 times with the same backoff, whatever state the endpoint is in.",
  },
  {
    key: "STRATEGY_B",
    short: "B",
    name: "Adaptive backoff",
    color: "#2563eb",
    blurb: "Waits longer as failures pile up, but has no idea whether the endpoint is healthy or dead.",
  },
  {
    key: "STRATEGY_C",
    short: "C",
    name: "AEDE",
    color: "#16a34a",
    blurb: "Tracks the endpoint's health and changes how it delivers, all the way to quarantine.",
  },
];

const STATE_COLORS = {
  AVAILABLE: "#22c55e",
  OBSERVED_DEGRADATION: "#eab308",
  UNSTABLE: "#f97316",
  QUARANTINED: "#ef4444",
  RECOVERING: "#3b82f6",
};

const STATE_ACTION = {
  AVAILABLE: "Delivering normally",
  OBSERVED_DEGRADATION: "Sending fewer requests at once",
  UNSTABLE: "One at a time, long gaps between retries",
  QUARANTINED: "Stopped sending; holding events for replay",
  RECOVERING: "Probing carefully after a restore",
};

const SPEEDS = [1, 2, 4];
const SECONDS_AT_1X = 30;

function phaseSummary(phase) {
  const parts = [];
  if (phase.http_5xx_rate > 0) parts.push(`${Math.round(phase.http_5xx_rate * 100)}% server errors`);
  if (phase.timed_out_rate > 0) parts.push(`${Math.round(phase.timed_out_rate * 100)}% timeouts`);
  if (!parts.length && phase.success_rate < 1) parts.push(`${Math.round((1 - phase.success_rate) * 100)}% failures`);
  if (phase.latency_ms >= 1000) parts.push(`${(phase.latency_ms / 1000).toFixed(1)} s responses`);
  if (!parts.length) parts.push("healthy");
  return parts.join(", ");
}

function phaseSeverity(phase) {
  const failing = 1 - phase.success_rate;
  if (failing >= 0.6) return "severe";
  if (failing > 0 || phase.latency_ms >= 1000) return "mild";
  return "healthy";
}

const PHASE_FILL = { healthy: "#22c55e14", mild: "#eab30822", severe: "#ef444422" };

export default function ScenarioLab() {
  const [scenarios, setScenarios] = useState([]);
  const [selectedId, setSelectedId] = useState(10);
  const [trace, setTrace] = useState(null);
  const [error, setError] = useState("");
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const timerRef = useRef(null);

  const loadTrace = useCallback(async (scenarioId) => {
    setPlaying(false);
    setIndex(0);
    setTrace(null);
    setError("");
    try {
      const response = await fetch(`${DASHBOARD_URL}/api/chart/scenarios/${scenarioId}/trace`);
      if (!response.ok) throw new Error(`Unable to load scenario (${response.status})`);
      setTrace(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load scenario.");
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch(`${DASHBOARD_URL}/api/chart/scenarios`);
        if (!response.ok) throw new Error(`Unable to load scenarios (${response.status})`);
        setScenarios((await response.json()).scenarios);
        await loadTrace(10);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to load scenarios.");
      }
    })();
  }, [loadTrace]);

  const total = trace?.scenario.total_events ?? 0;

  useEffect(() => {
    if (!playing || !total) return undefined;
    const tick = (SECONDS_AT_1X * 1000) / total / speed;
    timerRef.current = window.setInterval(() => {
      setIndex((current) => {
        if (current >= total) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, tick);
    return () => window.clearInterval(timerRef.current);
  }, [playing, speed, total]);

  const phaseRanges = useMemo(() => {
    if (!trace) return [];
    let start = 0;
    return trace.scenario.phases.map((phase, i) => {
      const range = { ...phase, index: i, start, end: start + phase.events };
      start += phase.events;
      return range;
    });
  }, [trace]);

  const byKey = useMemo(() => {
    const map = {};
    for (const strategy of trace?.strategies ?? []) map[strategy.strategy] = strategy;
    return map;
  }, [trace]);

  // One row per event: ERI (the same telemetry reaches every strategy) and
  // each strategy's running total of retries sent.
  const series = useMemo(() => {
    if (!trace) return [];
    const totals = { STRATEGY_A: 0, STRATEGY_B: 0, STRATEGY_C: 0 };
    return byKey.STRATEGY_C.steps.map((step, i) => {
      const row = { i: i + 1, eri: step.eri, state: step.state };
      for (const key of Object.keys(totals)) {
        totals[key] += byKey[key].steps[i].retries;
        row[key] = totals[key];
      }
      return row;
    });
  }, [trace, byKey]);

  const shown = series.slice(0, index);
  const currentPhase = phaseRanges.find((p) => index > p.start && index <= p.end) ?? phaseRanges[0];
  const finished = total > 0 && index >= total;
  const selected = scenarios.find((s) => s.scenario_id === selectedId);

  const counters = (key) => {
    const steps = (byKey[key]?.steps ?? []).slice(0, index);
    const delivered = steps.filter((s) => s.succeeded).length;
    return {
      retries: steps.reduce((sum, s) => sum + s.retries, 0),
      delay: steps.reduce((sum, s) => sum + s.delay_seconds, 0),
      success: steps.length ? Math.round((delivered / steps.length) * 100) : null,
      state: steps.length ? steps[steps.length - 1].state : "AVAILABLE",
    };
  };

  const pctFewer = (key) => {
    const c = byKey.STRATEGY_C?.total_wasted_retries ?? 0;
    const other = byKey[key]?.total_wasted_retries ?? 0;
    return other ? Math.round((1 - c / other) * 100) : null;
  };

  const groups = [
    { set: "held_out", title: "Held-out scenarios (used for the results)" },
    { set: "tuning", title: "Tuning scenarios (used to calibrate the score)" },
  ];

  return (
    <section className="lab-page">
      <article className="dashboard-card">
        <h2 className="card-title">
          Scenario Lab <HelpTip term="benchmark" /> <HelpTip term="strategies" />
        </h2>
        <p className="lab-intro">
          Pick a failure scenario and press play. The same stream of events hits a webhook endpoint
          that breaks and recovers on a script, and three delivery strategies handle it side by side.
          Watch how many retries each one fires at an endpoint that cannot answer. This replays the
          benchmark simulation event by event; the totals at the end are the reported numbers.
        </p>

        {groups.map((group) => (
          <div key={group.set} className="lab-group">
            <p className="lab-group-title">
              {group.title} {group.set === "held_out" && <HelpTip term="held_out" />}
            </p>
            <div className="lab-scenarios">
              {scenarios
                .filter((s) => s.evaluation_set === group.set)
                .map((s) => (
                  <button
                    key={s.scenario_id}
                    type="button"
                    className={`lab-scenario ${s.scenario_id === selectedId ? "active" : ""}`}
                    onClick={() => {
                      setSelectedId(s.scenario_id);
                      void loadTrace(s.scenario_id);
                    }}
                    title={s.description}
                  >
                    {s.name.replace(/^Scenario \d+: /, "")}
                    <span>#{s.scenario_id}</span>
                  </button>
                ))}
            </div>
          </div>
        ))}

        {selected && (
          <div className="lab-selected">
            <strong>{selected.name}</strong> · {selected.description}
          </div>
        )}

        {phaseRanges.length > 0 && (
          <div className="lab-phases" aria-label="Scenario phases">
            {phaseRanges.map((phase) => (
              <div
                key={phase.index}
                className={`lab-phase lab-phase-${phaseSeverity(phase)} ${currentPhase?.index === phase.index && index > 0 ? "current" : ""}`}
                style={{ flexGrow: phase.events }}
              >
                <b>{phase.description}</b>
                <span>
                  {phaseSummary(phase)}
                  {phase.manual_restore ? " · operator restores" : ""}
                </span>
              </div>
            ))}
          </div>
        )}

        {error && <div className="dlq-error">{error}</div>}
      </article>

      {trace && (
        <>
          <article className="dashboard-card lab-player">
            <div className="lab-controls">
              <button
                type="button"
                className="lab-play"
                onClick={() => {
                  if (finished) setIndex(0);
                  setPlaying((value) => !value);
                }}
              >
                {playing ? "⏸ Pause" : finished ? "↻ Play again" : index > 0 ? "▶ Resume" : "▶ Play"}
              </button>
              <button
                type="button"
                className="lab-secondary"
                onClick={() => {
                  setPlaying(false);
                  setIndex(0);
                }}
              >
                Reset
              </button>
              <button
                type="button"
                className="lab-secondary"
                onClick={() => {
                  setPlaying(false);
                  setIndex(total);
                }}
              >
                Skip to end
              </button>
              <div className="lab-speed">
                Speed
                {SPEEDS.map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={speed === value ? "active" : ""}
                    onClick={() => setSpeed(value)}
                  >
                    {value}×
                  </button>
                ))}
              </div>
              <div className="lab-progress">
                Event {index} / {total}
                <div className="lab-progress-bar">
                  <div style={{ width: `${total ? (index / total) * 100 : 0}%` }} />
                </div>
              </div>
            </div>

            <div className="lab-now">
              {index === 0 ? (
                <>Press play. Phase 1: <b>{phaseRanges[0]?.description}</b></>
              ) : (
                <>
                  Phase {currentPhase.index + 1} of {phaseRanges.length}: <b>{currentPhase.description}</b>
                  {" "}({phaseSummary(currentPhase)})
                </>
              )}
            </div>

            <div className="lab-charts">
              <div className="lab-chart">
                <p className="lab-chart-title">
                  Endpoint health, as every strategy sees it <HelpTip term="eri" />
                </p>
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={shown} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    {phaseRanges.map((phase) => (
                      <ReferenceArea
                        key={phase.index}
                        x1={phase.start + 1}
                        x2={phase.end}
                        fill={PHASE_FILL[phaseSeverity(phase)]}
                        ifOverflow="extendDomain"
                      />
                    ))}
                    <XAxis dataKey="i" type="number" domain={[1, total]} tick={{ fontSize: 11 }} />
                    <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
                    <Tooltip formatter={(value) => [Number(value).toFixed(1), "ERI"]} labelFormatter={(i) => `Event ${i}`} />
                    <Line type="monotone" dataKey="eri" stroke="#0f172a" strokeWidth={2} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
                <div className="lab-state-strip" title="Strategy C's endpoint state over time">
                  {shown.map((row) => (
                    <span key={row.i} style={{ background: STATE_COLORS[row.state], flexBasis: `${100 / total}%` }} />
                  ))}
                </div>
                <p className="lab-strip-caption">Strategy C's state for the endpoint, event by event</p>
              </div>

              <div className="lab-chart">
                <p className="lab-chart-title">
                  Retries fired so far <HelpTip term="wasted_retries" />
                </p>
                <ResponsiveContainer width="100%" height={232}>
                  <LineChart data={shown} margin={{ top: 8, right: 12, bottom: 0, left: -4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    {phaseRanges.map((phase) => (
                      <ReferenceArea
                        key={phase.index}
                        x1={phase.start + 1}
                        x2={phase.end}
                        fill={PHASE_FILL[phaseSeverity(phase)]}
                        ifOverflow="extendDomain"
                      />
                    ))}
                    <XAxis dataKey="i" type="number" domain={[1, total]} tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip labelFormatter={(i) => `Event ${i}`} />
                    <Legend />
                    {STRATEGIES.map((s) => (
                      <Line
                        key={s.key}
                        type="stepAfter"
                        dataKey={s.key}
                        name={`${s.short} · ${s.name}`}
                        stroke={s.color}
                        strokeWidth={2.5}
                        dot={false}
                        isAnimationActive={false}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </article>

          <div className="lab-lanes">
            {STRATEGIES.map((s) => {
              const c = counters(s.key);
              const isC = s.key === "STRATEGY_C";
              return (
                <article key={s.key} className="dashboard-card lab-lane" style={{ borderTopColor: s.color }}>
                  <h3>
                    <span style={{ color: s.color }}>{s.short}</span> · {s.name}
                  </h3>
                  <p className="lab-lane-blurb">{s.blurb}</p>
                  <div className="lab-lane-state">
                    {isC ? (
                      <>
                        <span className="lab-state-dot" style={{ background: STATE_COLORS[c.state] }} />
                        <b>{c.state}</b> · {STATE_ACTION[c.state]}
                      </>
                    ) : (
                      <>No health model: treats the endpoint the same, healthy or dead</>
                    )}
                  </div>
                  <div className="lab-lane-stats">
                    <div>
                      <b>{c.retries.toLocaleString()}</b>
                      <span>retries fired</span>
                    </div>
                    <div>
                      <b>{c.success == null ? "—" : `${c.success}%`}</b>
                      <span>delivered</span>
                    </div>
                    <div>
                      <b>{Math.round(c.delay).toLocaleString()} s</b>
                      <span>waiting added</span>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>

          {finished && (
            <article className="dashboard-card lab-summary">
              <h3>What happened</h3>
              <p>
                All three delivered the same share of events (
                {Math.round((byKey.STRATEGY_C.delivery_success_rate ?? 0) * 100)}%): no strategy can
                deliver to an endpoint that is down. The difference is effort. AEDE fired{" "}
                <b>{byKey.STRATEGY_C.total_wasted_retries.toLocaleString()}</b> retries against{" "}
                <b>{byKey.STRATEGY_A.total_wasted_retries.toLocaleString()}</b> for fixed retries
                {pctFewer("STRATEGY_A") != null && <> ({pctFewer("STRATEGY_A")}% fewer)</>} and{" "}
                <b>{byKey.STRATEGY_B.total_wasted_retries.toLocaleString()}</b> for adaptive backoff
                {pctFewer("STRATEGY_B") != null && <> ({pctFewer("STRATEGY_B")}% fewer)</>}
                {byKey.STRATEGY_C.quarantine_count > 0 && <>, because it quarantined the endpoint instead of hammering it</>}.
              </p>
              <p className="lab-caveat">
                The price: AEDE waits longer between retries, so its total added waiting is higher
                ({Math.round(byKey.STRATEGY_C.total_delay_seconds).toLocaleString()} s against{" "}
                {Math.round(byKey.STRATEGY_A.total_delay_seconds).toLocaleString()} s for A). That is
                deliberate: it backs off from a struggling server rather than piling on.
              </p>
            </article>
          )}
        </>
      )}
    </section>
  );
}
