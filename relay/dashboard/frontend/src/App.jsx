import { useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
} from "recharts";
import "./App.css";

const INITIAL_STATE = {
  type: "state_update",
  state: "AVAILABLE",
  eri: 100,
  eri_history: [],
  delivery_events: [],
  policy_decision: {
    decision: "No policy decision",
    selected_target: "",
    reason: "",
  },
  policy_explanation: {
    title: "No explanation available",
    reasons: [],
  },
};

function getWebSocketUrl() {
  return "ws://127.0.0.1:8001/ws";
}

function formatTime(timestamp) {
  if (!timestamp) {
    return "--";
  }

  const date = new Date(timestamp);

  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }

  return date.toLocaleTimeString();
}

function getStateClass(state) {
  switch (state) {
    case "AVAILABLE":
      return "status-available";

    case "OBSERVED_DEGRADATION":
      return "status-degraded";

    case "UNSTABLE":
      return "status-unstable";

    case "QUARANTINED":
      return "status-quarantined";

    case "RECOVERING":
      return "status-recovering";

    default:
      return "status-unknown";
  }
}

function getStateColor(state) {
  switch (state) {
    case "AVAILABLE":
      return "#22c55e";

    case "OBSERVED_DEGRADATION":
      return "#eab308";

    case "UNSTABLE":
      return "#f97316";

    case "QUARANTINED":
      return "#ef4444";

    case "RECOVERING":
      return "#3b82f6";

    default:
      return "#64748b";
  }
}

function App() {
  const [dashboardState, setDashboardState] = useState(INITIAL_STATE);
  const [connectionStatus, setConnectionStatus] =
    useState("connecting");
  const websocketRef = useRef(null);

const [failureRate, setFailureRate] = useState(80);
const [latencyMs, setLatencyMs] = useState(150);
const [failureMode, setFailureMode] = useState("mixed");

const scenarioPresets = [
  {
    id: "healthy",
    label: "Healthy",
    failureRate: 0,
    latencyMs: 150,
    failureMode: "mixed",
  },
  {
    id: "degraded",
    label: "Degraded",
    failureRate: 50,
    latencyMs: 1000,
    failureMode: "http_500",
  },
  {
    id: "outage",
    label: "Outage",
    failureRate: 100,
    latencyMs: 5000,
    failureMode: "timeout",
  },
];

  useEffect(() => {
    let websocket;
    let reconnectTimer;
    let stopped = false;

    const connect = () => {
      if (stopped) {
        return;
      }

      setConnectionStatus("connecting");

      websocket = new WebSocket(getWebSocketUrl());

      websocket.onopen = () => {
        websocketRef.current = websocket;
        setConnectionStatus("connected");
      };

      websocket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          console.log("Dashboard payload:", message);

          if (message?.type === "state_update") {
            setDashboardState((currentState) => ({
              ...currentState,
              ...message,
            }));
          }
        } catch (error) {
          console.error(
            "Unable to parse dashboard WebSocket message:",
            error,
          );
        }
      };

      websocket.onerror = () => {
        setConnectionStatus("error");
      };

      websocket.onclose = () => {
        if (websocketRef.current === websocket) {
          websocketRef.current = null;
        }

        if (stopped) {
          return;
        }

        setConnectionStatus("error");

        reconnectTimer = window.setTimeout(connect, 3000);
      };
    };

    connect();

    return () => {
      stopped = true;

      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
      }

      if (websocket) {
        websocket.close();
      }

      websocketRef.current = null;
    };
  }, []);

  const applyFaultInjection = () => {
  const websocket = websocketRef.current;

  if (!websocket || websocket.readyState !== WebSocket.OPEN) {
    return;
  }

  websocket.send(
    JSON.stringify({
      type: "demo_control",
      failure_rate: failureRate / 100,
      latency_ms: latencyMs,
      failure_mode: failureMode,
    }),
  );
};

const applyScenarioPreset = (preset) => {
  setFailureRate(preset.failureRate);
  setLatencyMs(preset.latencyMs);
  setFailureMode(preset.failureMode);

  const websocket = websocketRef.current;

  if (
    !websocket ||
    websocket.readyState !== WebSocket.OPEN
  ) {
    return;
  }

  websocket.send(
    JSON.stringify({
      type: "demo_control",
      failure_rate: preset.failureRate / 100,
      latency_ms: preset.latencyMs,
      failure_mode: preset.failureMode,
    }),
  );
};

  const chartData = useMemo(() => {
  const history = dashboardState.eri_history ?? [];

  return history.map((point, index) => {
    const previous = history[index - 1];

    return {
      ...point,
      index: index + 1,
      label: formatTime(point.timestamp),

      stateChanged:
        index > 0 &&
        previous?.state != null &&
        point.state != null &&
        previous.state !== point.state,

      successRate:
        Number(point.sub_scores?.success_rate ?? 0) * 100,

      timeoutRate:
        Number(point.sub_scores?.timeout_rate ?? 0) * 100,

      consecutiveFailures:
        Number(point.sub_scores?.consecutive_failures ?? 0) * 100,
    };
  });
}, [dashboardState.eri_history]);

const radarData = useMemo(() => {
  const scores = dashboardState.eri_sub_scores ?? {};
  const weights = dashboardState.eri_weights ?? {};

  const metrics = [
    { key: "success_rate", label: "Success Rate" },
    { key: "latency", label: "Latency" },
    { key: "consecutive_failures", label: "Consecutive Failures" },
    { key: "timeout_rate", label: "Timeout Rate" },
    { key: "http_5xx_rate", label: "HTTP 5xx" },
    { key: "queue_depth", label: "Queue Depth" },
    { key: "recovery_trend", label: "Recovery Trend" },
  ];

  return metrics.map((metric) => ({
    metric: metric.label,
    score: Math.max(
      0,
      Math.min(1, Number(scores[metric.key] ?? 0)),
    ),
    weight: Number(weights[metric.key] ?? 0),
  }));
}, [dashboardState.eri_sub_scores, dashboardState.eri_weights]);

const thresholds = dashboardState.thresholds ?? {};

const thresholdBands = useMemo(() => {
  const quarantine = Number(thresholds.quarantine_threshold);
  const unstable = Number(thresholds.unstable_threshold);
  const degradation = Number(thresholds.degradation_threshold);
  const degradationRecovery = Number(
    thresholds.degradation_recovery_threshold,
  );

  const values = [
    quarantine,
    unstable,
    degradation,
    degradationRecovery,
  ];

  if (
    values.some((value) => !Number.isFinite(value)) ||
    quarantine < 0 ||
    unstable <= quarantine ||
    degradation <= unstable ||
    degradationRecovery <= degradation ||
    degradationRecovery > 100
  ) {
    return [];
  }

  return [
    {
      key: "quarantined",
      from: 0,
      to: quarantine,
      color: "#ef444422",
    },
    {
      key: "unstable",
      from: quarantine,
      to: unstable,
      color: "#f9731622",
    },
    {
      key: "degraded",
      from: unstable,
      to: degradation,
      color: "#eab30822",
    },
    {
      key: "recovering",
      from: degradation,
      to: degradationRecovery,
      color: "#86efac22",
    },
    {
      key: "available",
      from: degradationRecovery,
      to: 100,
      color: "#22c55e22",
    },
  ];
}, [thresholds]);

  const stateClass = getStateClass(dashboardState.state);

  return (
    <main className="dashboard">
      <header className="dashboard-header">
        <div>
          <h1 className="dashboard-title">
            RelayForge Dashboard
          </h1>

          <p className="dashboard-subtitle">
            Endpoint reliability and delivery control-plane
            telemetry
          </p>
        </div>

        <div
          className={`connection-indicator ${connectionStatus}`}
        >
          <span className="connection-dot" />

          <span>
            {connectionStatus === "connected"
              ? "Connected"
              : connectionStatus === "connecting"
                ? "Connecting"
                : "Disconnected"}
          </span>
        </div>
      </header>

      <section className="dashboard-grid">
        <article className="dashboard-card state-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                Endpoint State
              </h2>

              <p className="card-description">
                Current endpoint operational state
              </p>
            </div>
          </div>

          <div className="state-content">
            <span
              className={`status-badge ${stateClass}`}
            >
              {dashboardState.state}
            </span>
          </div>
        </article>

        <article className="dashboard-card eri-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                Endpoint Reliability Index
              </h2>

              <p className="card-description">
                Current composite reliability score
              </p>
            </div>
          </div>

          <div className="eri-value">
            {dashboardState.eri}
            <span className="eri-scale">/ 100</span>
          </div>

          <div className="metric-label">
            Higher values indicate better endpoint
            reliability.
          </div>
        </article>

        <article className="dashboard-card stream-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                Live Stream
              </h2>

              <p className="card-description">
                WebSocket dashboard connection
              </p>
            </div>
          </div>

          <div className="stream-message">
            {connectionStatus === "connected"
              ? "Receiving live state updates."
              : connectionStatus === "connecting"
                ? "Waiting for dashboard stream..."
                : "Dashboard stream unavailable."}
          </div>
        </article>

        <article className="dashboard-card fault-card">
  <div className="card-header">
    <div>
      <h2 className="card-title">
        Fault Injection
      </h2>

      <p className="card-description">
        Configure the dashboard demo telemetry
      </p>
    </div>
  </div>

  <div className="fault-controls">
    <label className="fault-control">
      <span className="fault-label">
        Failure rate
      </span>

      <div className="fault-range-row">
        <input
          type="range"
          min="0"
          max="100"
          step="10"
          value={failureRate}
          onChange={(event) =>
            setFailureRate(Number(event.target.value))
          }
        />

        <span className="fault-value">
          {failureRate}%
        </span>
      </div>
    </label>

    <label className="fault-control">
      <span className="fault-label">
        Latency
      </span>

      <div className="fault-range-row">
        <input
          type="range"
          min="50"
          max="5000"
          step="50"
          value={latencyMs}
          onChange={(event) =>
            setLatencyMs(Number(event.target.value))
          }
        />

        <span className="fault-value">
          {latencyMs} ms
        </span>
      </div>
    </label>

    <label className="fault-control">
      <span className="fault-label">
        Failure mode
      </span>

      <select
        className="fault-select"
        value={failureMode}
        onChange={(event) =>
          setFailureMode(event.target.value)
        }
      >
        <option value="mixed">
          Mixed
        </option>

        <option value="http_500">
          HTTP 500
        </option>

        <option value="timeout">
          Timeout
        </option>
      </select>
    </label>

    <button
      className="fault-apply"
      type="button"
      onClick={applyFaultInjection}
      disabled={connectionStatus !== "connected"}
    >
      Apply fault profile
    </button>

<div className="scenario-presets">
  <p className="fault-label">Scenario presets</p>

  <div className="scenario-preset-buttons">
    {scenarioPresets.map((preset) => (
      <button
        key={preset.id}
        type="button"
        className="scenario-preset-button"
        onClick={() => applyScenarioPreset(preset)}
        disabled={connectionStatus !== "connected"}
      >
        {preset.label}
      </button>
    ))}
  </div>
</div>
  </div>
</article>

        <article className="dashboard-card chart-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                ERI History
              </h2>

              <p className="card-description">
                Recent endpoint reliability trend
              </p>
            </div>
          </div>

          <div className="chart-container">
  {chartData.length === 0 ? (
    <div className="chart-empty">
      No ERI history available.
    </div>
  ) : (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart
        data={chartData}
        margin={{ top: 12, right: 16, bottom: 8, left: 0 }}
      >
        <CartesianGrid strokeDasharray="3 3" />

        {thresholdBands.map((band) => (
          <ReferenceArea
            key={band.key}
            y1={band.from}
            y2={band.to}
            fill={band.color}
            stroke="none"
          />
        ))}

        <XAxis
          dataKey="label"
          tick={{ fontSize: 11 }}
          minTickGap={24}
        />

        <YAxis
          domain={[0, 100]}
          ticks={[0, 20, 40, 60, 80, 100]}
          tick={{ fontSize: 11 }}
        />

        <Tooltip
          formatter={(value, name) => [
            Number(value).toFixed(1),
            name,
          ]}
          labelFormatter={(label) => `Time: ${label}`}
        />

        {Number.isFinite(Number(thresholds.degradation_threshold)) &&
          thresholds.degradation_threshold !== undefined && (
            <ReferenceLine
              y={Number(thresholds.degradation_threshold)}
              stroke="#eab308"
              strokeDasharray="6 4"
              label={{
                value: "Degradation",
                position: "insideTopRight",
                fontSize: 10,
              }}
            />
          )}

        {Number.isFinite(
          Number(thresholds.degradation_recovery_threshold),
        ) &&
          thresholds.degradation_recovery_threshold !== undefined && (
            <ReferenceLine
              y={Number(thresholds.degradation_recovery_threshold)}
              stroke="#22c55e"
              strokeDasharray="6 4"
              label={{
                value: "Recovery",
                position: "insideTopRight",
                fontSize: 10,
              }}
            />
          )}

        <Line
          type="monotone"
          dataKey="value"
          name="ERI"
          stroke={getStateColor(dashboardState.state)}
          strokeWidth={2.5}
          isAnimationActive={false}
          activeDot={{ r: 5 }}
          dot={(props) => {
            const { cx, cy, payload } = props;

            if (!payload?.stateChanged) {
              return null;
            }

            return (
              <circle
                key={`transition-${payload.index}`}
                cx={cx}
                cy={cy}
                r={4}
                fill={getStateColor(payload.state)}
                stroke="#ffffff"
                strokeWidth={1.5}
              />
            );
          }}
        />
      </LineChart>
    </ResponsiveContainer>
  )}
</div>

<div className="eri-chart-legend">
  <span>
    <span className="legend-dot" style={{ background: "#22c55e" }} />
    Available
  </span>
  <span>
    <span className="legend-dot" style={{ background: "#eab308" }} />
    Degraded
  </span>
  <span>
    <span className="legend-dot" style={{ background: "#f97316" }} />
    Unstable
  </span>
  <span>
    <span className="legend-dot" style={{ background: "#ef4444" }} />
    Quarantined
  </span>
  <span>
    <span className="legend-dot" style={{ background: "#3b82f6" }} />
    Recovering
  </span>
</div>

<div className="eri-sparklines">
  {[
    {
      key: "successRate",
      label: "Success Rate",
      color: "#22c55e",
    },
    {
      key: "timeoutRate",
      label: "Timeout Rate",
      color: "#f97316",
    },
    {
      key: "consecutiveFailures",
      label: "Consecutive Failures",
      color: "#ef4444",
    },
  ].map((metric) => (
    <div className="eri-sparkline" key={metric.key}>
      <div className="sparkline-header">
        <span>{metric.label}</span>
        <strong>
          {chartData.length > 0
            ? `${chartData[chartData.length - 1][metric.key].toFixed(1)}%`
            : "--"}
        </strong>
      </div>

      <div className="sparkline-chart">
        {chartData.length === 0 ? (
          <div className="sparkline-empty">
            No data
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <Line
                type="monotone"
                dataKey={metric.key}
                stroke={metric.color}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  ))}
</div>
        </article>

        <article className="dashboard-card radar-card">
  <div className="card-header">
    <div>
      <h2 className="card-title">ERI Sub-Metrics</h2>
      <p className="card-description">
        Normalized reliability scores across seven factors
      </p>
    </div>
  </div>

  <div className="radar-chart-container">
    {dashboardState.eri_sub_scores ? (
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={radarData} outerRadius="70%">
          <PolarGrid />

          <PolarAngleAxis
            dataKey="metric"
            tick={{ fontSize: 10 }}
          />

          <PolarRadiusAxis
            angle={90}
            domain={[0, 1]}
            tick={{ fontSize: 9 }}
          />

          <Radar
            name="ERI score"
            dataKey="score"
            stroke="#3b82f6"
            fill="#3b82f6"
            fillOpacity={0.3}
          />

          <Tooltip
            formatter={(value) => [
              `${(Number(value) * 100).toFixed(1)}%`,
              "Normalized score",
            ]}
          />
        </RadarChart>
      </ResponsiveContainer>
    ) : (
      <div className="chart-empty">
        Waiting for ERI sub-metric data.
      </div>
    )}
  </div>
</article>

        <article className="dashboard-card timeline-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                Delivery Timeline
              </h2>

              <p className="card-description">
                Recent delivery activity
              </p>
            </div>
          </div>

          <div className="timeline">
            {dashboardState.delivery_events?.length === 0 ? (
              <div className="chart-empty">
                No delivery events available.
              </div>
            ) : (
              dashboardState.delivery_events.map((event) => (
  <div
    className="timeline-event"
    key={event.id}
  >
    <div className="timeline-main">
      <span className="timeline-time">
        {formatTime(event.timestamp)}
      </span>

      <span
        className={`timeline-status ${
          event.success
            ? "timeline-success"
            : "timeline-failure"
        }`}
      >
        {event.type}
      </span>
    </div>

    <div className="timeline-details">
      <span>
        Attempt #{event.attempt_number ?? "--"}
      </span>

      {event.http_status != null && (
        <span>
          HTTP {event.http_status}
        </span>
      )}

      {event.status && (
        <span>
          {event.status}
        </span>
      )}

      {event.delivery_id && (
        <span className="timeline-mono">
          Delivery: {event.delivery_id}
        </span>
      )}

      {event.error_message && (
        <span className="timeline-error">
          {event.error_message}
        </span>
      )}
    </div>
  </div>
))
            )}
          </div>
        </article>

        <article className="dashboard-card policy-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                Policy Decision
              </h2>

              <p className="card-description">
                Current delivery policy selection
              </p>
            </div>
          </div>

          <div className="policy-decision">
  {!dashboardState.policy_decision ? (
    <div className="chart-empty">
      No policy decision available.
    </div>
  ) : (
    <>
      <div className="policy-row">
        <span className="policy-label">
          Decision
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.decision ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Selected target
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.selected_target || "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Policy ID
        </span>

        <span className="policy-value policy-mono">
          {dashboardState.policy_decision.policy_id || "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          ERI score
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.eri_score ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Max parallelism
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.max_parallel ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Queue priority
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.queue_priority ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Throughput limit
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.throughput_limit ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Max retries
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.max_retries ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Backoff
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.backoff_strategy || "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Retry delay
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.initial_delay_seconds ?? "--"}
          {" – "}
          {dashboardState.policy_decision.max_delay_seconds ?? "--"}
          {" seconds"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Isolation
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.isolation_action || "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Reason
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.reason || "--"}
        </span>
      </div>
    </>
  )}
</div>
        </article>

        <article className="dashboard-card explanation-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                Policy Explainability
              </h2>

              <p className="card-description">
                Why the current policy was selected
              </p>
            </div>
          </div>

          <div>
  {!dashboardState.policy_explanation ? (
    <div className="chart-empty">
      No policy explanation available.
    </div>
  ) : (
    <>
      <h3 className="explanation-title">
        {dashboardState.policy_explanation.title ||
          "Policy explanation"}
      </h3>

      <div className="explanation-grid">
        <div className="explanation-item">
          <span className="explanation-label">
            State before
          </span>

          <span className="explanation-value">
            {dashboardState.policy_explanation.state_before ||
              "--"}
          </span>
        </div>

        <div className="explanation-item">
          <span className="explanation-label">
            State after
          </span>

          <span className="explanation-value">
            {dashboardState.policy_explanation.state_after ||
              "--"}
          </span>
        </div>

        <div className="explanation-item">
          <span className="explanation-label">
            State changed
          </span>

          <span className="explanation-value">
            {dashboardState.policy_explanation.state_changed
              ? "Yes"
              : "No"}
          </span>
        </div>

        <div className="explanation-item">
          <span className="explanation-label">
            Strategy
          </span>

          <span className="explanation-value">
            {dashboardState.policy_explanation.strategy_used ||
              "--"}
          </span>
        </div>

        <div className="explanation-item">
          <span className="explanation-label">
            Window size
          </span>

          <span className="explanation-value">
            {dashboardState.policy_explanation.window_size ?? "--"}
          </span>
        </div>

        <div className="explanation-item">
          <span className="explanation-label">
            Evaluated
          </span>

          <span className="explanation-value">
            {formatTime(
              dashboardState.policy_explanation.evaluated_at,
            )}
          </span>
        </div>
      </div>

      <div className="explanation-section">
        <h4 className="explanation-section-title">
          Triggered thresholds
        </h4>

        {dashboardState.policy_explanation
          .triggered_thresholds?.length ? (
          <ul className="explanation-list">
            {dashboardState.policy_explanation.triggered_thresholds.map(
              (threshold, index) => (
                <li key={`${threshold}-${index}`}>
                  {threshold}
                </li>
              ),
            )}
          </ul>
        ) : (
          <div className="explanation-empty">
            No thresholds triggered.
          </div>
        )}
      </div>

      <div className="explanation-section">
        <h4 className="explanation-section-title">
          Policy changes
        </h4>

        {dashboardState.policy_explanation
          .policy_changes?.length ? (
          <ul className="explanation-list">
            {dashboardState.policy_explanation.policy_changes.map(
              (change, index) => (
                <li key={`${change}-${index}`}>
                  {change}
                </li>
              ),
            )}
          </ul>
        ) : (
          <div className="explanation-empty">
            No policy changes recorded.
          </div>
        )}
      </div>

      <div className="explanation-section">
        <h4 className="explanation-section-title">
          Explanation
        </h4>

        {dashboardState.policy_explanation.reasons?.length ? (
          <ul className="explanation-list">
            {dashboardState.policy_explanation.reasons.map(
              (reason, index) => (
                <li key={`${reason}-${index}`}>
                  {reason}
                </li>
              ),
            )}
          </ul>
        ) : (
          <div className="explanation-empty">
            No explanation details available.
          </div>
        )}
      </div>

      {Object.keys(
        dashboardState.policy_explanation.metrics ?? {},
      ).length > 0 && (
        <div className="explanation-section">
          <h4 className="explanation-section-title">
            Metric snapshot
          </h4>

          <div className="explanation-metrics">
            {Object.entries(
              dashboardState.policy_explanation.metrics,
            ).map(([name, value]) => (
              <div className="explanation-metric" key={name}>
                <span className="explanation-label">
                  {name}
                </span>

                <span className="explanation-value">
                  {Number(value).toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {Object.keys(
        dashboardState.policy_explanation.thresholds ?? {},
      ).length > 0 && (
        <div className="explanation-section">
          <h4 className="explanation-section-title">
            Threshold configuration
          </h4>

          <div className="explanation-metrics">
            {Object.entries(
              dashboardState.policy_explanation.thresholds,
            ).map(([name, value]) => (
              <div className="explanation-metric" key={name}>
                <span className="explanation-label">
                  {name}
                </span>

                <span className="explanation-value">
                  {Number(value).toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )}
</div>
        </article>
      </section>
    </main>
  );
}

export default App;