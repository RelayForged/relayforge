import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
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
  return "ws://127.0.0.1:8000/ws";
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

function App() {
  const [dashboardState, setDashboardState] = useState(INITIAL_STATE);
  const [connectionStatus, setConnectionStatus] =
    useState("connecting");

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
        setConnectionStatus("connected");
      };

      websocket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);

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
    };
  }, []);

  const chartData = useMemo(() => {
    return (dashboardState.eri_history ?? []).map(
      (point, index) => ({
        ...point,
        index: index + 1,
        label: formatTime(point.timestamp),
      }),
    );
  }, [dashboardState.eri_history]);

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
              <ResponsiveContainer
                width="100%"
                height="100%"
              >
                <LineChart data={chartData}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                  />

                  <XAxis
                    dataKey="label"
                    tick={{ fontSize: 11 }}
                  />

                  <YAxis
                    domain={[0, 100]}
                    tick={{ fontSize: 11 }}
                  />

                  <Tooltip />

                  <Line
                    type="monotone"
                    dataKey="value"
                    stroke="currentColor"
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
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