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
import {
  demoDeliveryEvents,
  demoPolicyDecision,
  demoPolicyExplanation,
} from "./demoData";

const WS_URL = "ws://127.0.0.1:8001/ws";

function formatTime(timestamp) {
  if (!timestamp) {
    return "--";
  }

  const date = new Date(timestamp);

  if (Number.isNaN(date.getTime())) {
    return "--";
  }

  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function getStatusClass(state) {
  const normalizedState = String(state || "").toLowerCase();

  const classes = {
    available: "status-available",
    degraded: "status-degraded",
    unstable: "status-unstable",
    quarantined: "status-quarantined",
    recovering: "status-recovering",
  };

  return classes[normalizedState] || "status-unknown";
}

function App() {
  const [dashboardState, setDashboardState] = useState({
    state: "UNKNOWN",
    eri: null,
    eri_history: [],
  });

  const [connectionStatus, setConnectionStatus] = useState("connecting");
  const [lastUpdate, setLastUpdate] = useState(null);

  useEffect(() => {
    let socket;
    let reconnectTimer;
    let stopped = false;

    const connect = () => {
      if (stopped) {
        return;
      }

      setConnectionStatus("connecting");

      socket = new WebSocket(WS_URL);

      socket.onopen = () => {
        setConnectionStatus("connected");
      };

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);

          if (message.type !== "state_update") {
            return;
          }

          setDashboardState({
            state: message.state || "UNKNOWN",
            eri:
              typeof message.eri === "number"
                ? message.eri
                : null,
            eri_history: Array.isArray(message.eri_history)
              ? message.eri_history
              : [],
          });

          setLastUpdate(new Date());
        } catch {
          setConnectionStatus("error");
        }
      };

      socket.onerror = () => {
        setConnectionStatus("error");
      };

      socket.onclose = () => {
        if (stopped) {
          return;
        }

        setConnectionStatus("error");

        reconnectTimer = window.setTimeout(() => {
          connect();
        }, 3000);
      };
    };

    connect();

    return () => {
      stopped = true;

      window.clearTimeout(reconnectTimer);

      if (socket) {
        socket.close();
      }
    };
  }, []);

  const chartData = useMemo(() => {
    return dashboardState.eri_history.map((point) => ({
      ...point,
      time: formatTime(point.timestamp),
    }));
  }, [dashboardState.eri_history]);

  const connectionLabel = {
    connected: "Live",
    connecting: "Connecting",
    error: "Disconnected",
  }[connectionStatus];

  return (
    <main className="dashboard">
      <header className="dashboard-header">
        <div>
          <h1 className="dashboard-title">RelayForge Dashboard</h1>
          <p className="dashboard-subtitle">
            Reliability, delivery, policy, and explainability overview
          </p>
        </div>

        <div className={`connection-indicator ${connectionStatus}`}>
          <span className="connection-dot" />
          {connectionLabel}
        </div>
      </header>

      <section className="dashboard-grid">
        <article className="dashboard-card state-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Endpoint State</h2>
              <p className="card-description">
                Current endpoint availability
              </p>
            </div>
          </div>

          <div className="state-content">
            <span
              className={`status-badge ${getStatusClass(
                dashboardState.state,
              )}`}
            >
              {dashboardState.state}
            </span>
          </div>
        </article>

        <article className="dashboard-card eri-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Endpoint Reliability Index</h2>
              <p className="card-description">
                Current reliability score
              </p>
            </div>
          </div>

          <div>
            <span className="eri-value">
              {dashboardState.eri ?? "--"}
            </span>
            <span className="eri-scale">/ 100</span>
          </div>

          <div className="metric-label">
            Higher values indicate better observed reliability.
          </div>
        </article>

        <article className="dashboard-card stream-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Dashboard Stream</h2>
              <p className="card-description">
                FastAPI WebSocket connection
              </p>
            </div>
          </div>

          <div className="stream-message">
            {connectionStatus === "connected" &&
              "Receiving live state updates."}

            {connectionStatus === "connecting" &&
              "Connecting to dashboard backend..."}

            {connectionStatus === "error" &&
              "Dashboard backend is unavailable."}
          </div>

          <div className="metric-label">
            Last update:{" "}
            {lastUpdate ? formatTime(lastUpdate) : "--"}
          </div>
        </article>

        <article className="dashboard-card chart-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">ERI History</h2>
              <p className="card-description">
                Recent reliability observations
              </p>
            </div>
          </div>

          <div className="chart-container">
            {chartData.length === 0 ? (
              <div className="chart-empty">
                Waiting for ERI history...
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={chartData}
                  margin={{
                    top: 8,
                    right: 8,
                    left: -20,
                    bottom: 8,
                  }}
                >
                  <CartesianGrid strokeDasharray="3 3" />

                  <XAxis
                    dataKey="time"
                    tick={{ fontSize: 11 }}
                    minTickGap={30}
                  />

                  <YAxis
                    domain={[0, 100]}
                    tick={{ fontSize: 11 }}
                  />

                  <Tooltip />

                  <Line
                    type="monotone"
                    dataKey="value"
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </article>

        <article className="dashboard-card timeline-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Delivery Timeline</h2>
              <p className="card-description">
                Recent delivery activity
              </p>
            </div>

            <span className="demo-label">Demo</span>
          </div>

          <div className="timeline">
            {demoDeliveryEvents.map((event) => (
              <div
                className="timeline-event"
                key={`${event.timestamp}-${event.type}`}
              >
                <span className="timeline-time">
                  {event.timestamp}
                </span>

                <span className="timeline-type">
                  {event.type}
                </span>
              </div>
            ))}
          </div>
        </article>

        <article className="dashboard-card policy-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Policy Decision</h2>
              <p className="card-description">
                Current routing decision
              </p>
            </div>

            <span className="demo-label">Demo</span>
          </div>

          <div className="policy-decision">
            <div className="policy-row">
              <span className="policy-label">Decision</span>
              <span className="policy-value">
                {demoPolicyDecision.decision}
              </span>
            </div>

            <div className="policy-row">
              <span className="policy-label">Selected target</span>
              <span className="policy-value">
                {demoPolicyDecision.selectedTarget}
              </span>
            </div>

            <div className="policy-row">
              <span className="policy-label">Reason</span>
              <span className="policy-value">
                {demoPolicyDecision.reason}
              </span>
            </div>
          </div>
        </article>

        <article className="dashboard-card explanation-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Explainability</h2>
              <p className="card-description">
                Why the current decision was made
              </p>
            </div>

            <span className="demo-label">Demo</span>
          </div>

          <div>
            <h3 className="explanation-title">
              Decision factors
            </h3>

            <ul className="explanation-list">
              {demoPolicyExplanation.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        </article>
      </section>
    </main>
  );
}

export default App;