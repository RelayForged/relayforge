import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
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
import BenchmarkTab from "./BenchmarkTab";
import HelpTip from "./HelpTip";
import NoisyNeighbour from "./NoisyNeighbour";
import ScenarioLab from "./ScenarioLab";
import { SCENARIO_GUIDE, STATE_BEHAVIOUR } from "./glossary";
import {
  API_URL,
  DASHBOARD_URL,
  DASHBOARD_WS_URL,
  FAULT_MODES,
  SIMULATOR_URL,
} from "./config";

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
  return DASHBOARD_WS_URL;
}

function formatEri(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(1) : "--";
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
const [activeScenario, setActiveScenario] = useState(null);
// The open tab follows the URL hash (#benchmark, #multi-endpoint, #lab),
// so a tab can be linked to and survives a reload.
const TABS = ["dashboard", "benchmark", "multi-endpoint", "lab"];
const [activeTab, setActiveTabState] = useState(() => {
  const fromHash = window.location.hash.replace("#", "");
  return TABS.includes(fromHash) ? fromHash : "dashboard";
});
const setActiveTab = (tab) => {
  setActiveTabState(tab);
  window.history.replaceState(null, "", tab === "dashboard" ? window.location.pathname : `#${tab}`);
};
const [dlqItems, setDlqItems] = useState([]);
const [dlqLoading, setDlqLoading] = useState(false);
const [dlqError, setDlqError] = useState("");
const [replayingDeliveryId, setReplayingDeliveryId] = useState(null);
const [stateTransitions, setStateTransitions] = useState([]);
const [selectedDlqIds, setSelectedDlqIds] = useState([]);
const [alerts, setAlerts] = useState([]);
const [signatures, setSignatures] = useState({});

// Alerts close themselves after a while; the journal keeps the history.
const ALERT_LIFETIME_MS = 15000;
useEffect(() => {
  if (!alerts.length) {
    return undefined;
  }
  const timer = window.setInterval(() => {
    const cutoff = Date.now() - ALERT_LIFETIME_MS;
    setAlerts((previous) => {
      const kept = previous.filter((item) => (item.createdAt ?? Date.now()) > cutoff);
      return kept.length === previous.length ? previous : kept;
    });
  }, 1000);
  return () => window.clearInterval(timer);
}, [alerts.length]);
const [faultError, setFaultError] = useState("");
const [aedeAvailable, setAedeAvailable] = useState(true);
const [aedeSwitchError, setAedeSwitchError] = useState("");
const previousStateRef = useRef(INITIAL_STATE.state);

const scenarioPresets = [
  { id: "normal", label: "▶ Normal" },
  { id: "early_degradation", label: "⚠ Early Degradation" },
  { id: "instability", label: "🔴 Instability" },
  { id: "full_outage", label: "💀 Full Outage" },
  { id: "recovery", label: "🔄 Recovery" },
  { id: "rate_limiting", label: "⚡ Rate Limiting" },
  { id: "auth_failure", label: "🔑 Auth Failure" },
  { id: "network_partition", label: "📡 Network Partition" },
];

const recordStateTransition = (update) => {
  if (!update?.policy_explanation?.state_changed) {
    return;
  }

  const explanation = update.policy_explanation;

  const transition = {
    id:
      explanation.record_id ??
      `${explanation.evaluated_at}-${explanation.state_before}-${explanation.state_after}`,
    evaluatedAt: explanation.evaluated_at,
eri: update.eri,
stateBefore: explanation.state_before,
stateAfter: explanation.state_after,
    triggeredThresholds: explanation.triggered_thresholds ?? [],
    strategyUsed: explanation.strategy_used ?? "--",
    reason: explanation.reason ?? "--",
  };

  setStateTransitions((previous) => {
    if (previous.some((item) => item.id === transition.id)) {
      return previous;
    }

    return [transition, ...previous].slice(0, 50);
  });
};

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

          if (message?.type === "state_update") {
            recordStateTransition(message);

            const nextState = message.state;
            const previousState = previousStateRef.current;
            if (nextState !== previousState) {
              if (nextState === "QUARANTINED" || nextState === "UNSTABLE") {
                const explanation = message.policy_explanation ?? {};
                const nextAlert = {
                  id: `${nextState}-${explanation.record_id ?? Date.now()}`,
                  createdAt: Date.now(),
                  state: nextState,
                  endpointId: message.endpoint_id ?? "Current endpoint",
                  eri: formatEri(message.eri),
                  successRate:
                    message.eri_sub_scores?.success_rate != null
                      ? `${(message.eri_sub_scores.success_rate * 100).toFixed(0)}%`
                      : "--",
                  title: nextState === "QUARANTINED" ? "Endpoint quarantined" : "Endpoint unstable",
                  message: explanation.reason ?? "",
                };
                setAlerts((previousAlerts) => [nextAlert, ...previousAlerts].slice(0, 5));
              }
              previousStateRef.current = nextState;
            }

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


  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch(`${API_URL}/demo/status`);
        if (response.ok) {
          const status = await response.json();
          setAedeAvailable(status.aede_available ?? true);
        }
      } catch {
        // The switch defaults to "available"; the tier banner shows the truth.
      }
    })();
  }, []);

  const setAedeAvailability = async (available) => {
    setAedeSwitchError("");
    try {
      const response = await fetch(`${API_URL}/demo/aede`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ available }),
      });
      if (!response.ok) {
        throw new Error(`AEDE switch failed (${response.status})`);
      }
      setAedeAvailable(available);
    } catch (error) {
      setAedeSwitchError(
        error instanceof Error ? error.message : "AEDE switch failed.",
      );
    }
  };

  const [restoreNotice, setRestoreNotice] = useState("");

  const restoreEndpoint = async () => {
    const endpointId = dashboardState.endpoint_id;
    if (!endpointId) {
      return;
    }
    setRestoreNotice("");
    try {
      const response = await fetch(
        `${API_URL}/endpoints/${encodeURIComponent(endpointId)}/restore`,
        { method: "POST" },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : JSON.stringify(payload.detail ?? payload),
        );
      }
      setRestoreNotice(
        "Restore requested: the endpoint is on probation (RECOVERING). Replay held events from the dead letter queue.",
      );
    } catch (error) {
      setAlerts((previousAlerts) => [
        {
          id: `restore-${Date.now()}`,
          createdAt: Date.now(),
          state: "RESTORE_ERROR",
          endpointId,
          eri: formatEri(dashboardState.eri),
          successRate: "--",
          title: "Restore request failed",
          message: error instanceof Error ? error.message : "Unable to restore endpoint.",
        },
        ...previousAlerts,
      ].slice(0, 5));
    }
  };

  const applyFaultInjection = () => {
  const selectedMode = FAULT_MODES.find((mode) => mode.value === failureMode);

  if (dashboardState.source !== "synthetic") {
    setFaultError("");
    void (async () => {
      try {
        const response = await fetch(`${SIMULATOR_URL}/simulator/config`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            mode: selectedMode?.simulator ?? failureMode,
            rate: failureRate / 100,
            latency_ms: latencyMs,
          }),
        });

        if (!response.ok) {
          throw new Error(`Simulator rejected the profile (${response.status})`);
        }

        setActiveScenario(null);
      } catch (error) {
        console.error("Unable to configure simulator:", error);
        setFaultError(
          error instanceof Error ? error.message : "Unable to configure simulator.",
        );
      }
    })();
    return;
  }

  if (!selectedMode?.synthetic) {
    setFaultError("This failure mode needs the live stack.");
    return;
  }
  setFaultError("");

  const websocket = websocketRef.current;

  if (!websocket || websocket.readyState !== WebSocket.OPEN) {
    return;
  }

  websocket.send(
    JSON.stringify({
      type: "demo_control",
      failure_rate: failureRate / 100,
      latency_ms: latencyMs,
      failure_mode: selectedMode.synthetic,
    }),
  );
};

const applyScenarioPreset = (preset) => {
  setActiveScenario(preset.id);

  if (dashboardState.source === "synthetic") {
    const websocket = websocketRef.current;

    if (!websocket || websocket.readyState !== WebSocket.OPEN) {
      return;
    }

    const scenarioProfiles = {
  normal: {
    failure_rate: 0.0,
    latency_ms: 150,
    failure_mode: "mixed",
  },
  early_degradation: {
    failure_rate: 0.4,
    latency_ms: 500,
    failure_mode: "mixed",
  },
  instability: {
    failure_rate: 0.75,
    latency_ms: 750,
    failure_mode: "http_500",
  },
  full_outage: {
    failure_rate: 1.0,
    latency_ms: 1500,
    failure_mode: "timeout",
  },
  recovery: {
    failure_rate: 0.0,
    latency_ms: 150,
    failure_mode: "mixed",
  },
  rate_limiting: {
    failure_rate: 0.8,
    latency_ms: 150,
    failure_mode: "rate_limit",
  },
  auth_failure: {
    failure_rate: 1.0,
    latency_ms: 150,
    failure_mode: "auth_failure",
  },
  network_partition: {
    failure_rate: 1.0,
    latency_ms: 150,
    failure_mode: "connection_reset",
  },
};

    const profile = scenarioProfiles[preset.id];
    if (!profile) {
      return;
    }

    websocket.send(
      JSON.stringify({
        type: "demo_control",
        ...profile,
      }),
    );

    setFailureRate(Math.round(profile.failure_rate * 100));
    setLatencyMs(profile.latency_ms);
    setFailureMode(
      FAULT_MODES.find((mode) => mode.synthetic === profile.failure_mode)?.value ??
        "mixed",
    );
    return;
  }

  void (async () => {
    try {
      const response = await fetch(`${API_URL}/demo/scenario`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          scenario: preset.id,
        }),
      });

      const payload = await response.json();

      if (!response.ok) {
        const detail =
          typeof payload.detail === "string"
            ? payload.detail
            : JSON.stringify(payload.detail ?? payload);

        throw new Error(detail);
      }

      setActiveScenario(payload.active_scenario ?? preset.id);

      if (payload.simulator) {
        setFailureMode(payload.simulator.mode);
        setFailureRate(Math.round(payload.simulator.rate * 100));
        setLatencyMs(payload.simulator.latency_ms);
      }
    } catch (error) {
      console.error("Unable to activate demo scenario:", error);
      setActiveScenario(null);
    }
  })();
};

const loadDlq = useCallback(async () => {
  if (dashboardState.source === "synthetic") {
    setDlqItems([]);
    setDlqError("");
    setDlqLoading(false);
    return;
  }

  const endpointId = dashboardState.endpoint_id;

  if (!endpointId) {
    setDlqItems([]);
    return;
  }

  setDlqLoading(true);
  setDlqError("");

  try {
    const response = await fetch(
      `${DASHBOARD_URL}/api/chart/dlq?limit=50&endpoint_id=${encodeURIComponent(endpointId)}`,
    );

    if (!response.ok) {
      throw new Error(`Unable to load DLQ (${response.status})`);
    }

    const payload = await response.json();

    setDlqItems(Array.isArray(payload.items) ? payload.items : []);
  } catch (error) {
    console.error("Unable to load DLQ:", error);
    setDlqError(
      error instanceof Error
        ? error.message
        : "Unable to load dead-letter messages.",
    );
  } finally {
    setDlqLoading(false);
  }
}, [dashboardState.source, dashboardState.endpoint_id]);

const replayDeadLetter = async (deliveryId) => {
  setReplayingDeliveryId(deliveryId);
  setDlqError("");

  try {
    const response = await fetch(`${API_URL}/replay`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        delivery_ids: [deliveryId],
      }),
    });

    const payload = await response.json();

    if (!response.ok) {
      const detail =
        typeof payload.detail === "string"
          ? payload.detail
          : JSON.stringify(payload.detail ?? payload);

      throw new Error(detail);
    }

    await loadDlq();
  } catch (error) {
    console.error("Unable to replay dead letter:", error);
    setDlqError(
      error instanceof Error
        ? error.message
        : "Unable to request replay.",
    );
  } finally {
    setReplayingDeliveryId(null);
  }
};

const replaySelectedDeadLetters = async () => {
  if (selectedDlqIds.length === 0) {
    return;
  }

  setReplayingDeliveryId("__selected__");
  setDlqError("");

  try {
    const response = await fetch(`${API_URL}/replay`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        delivery_ids: selectedDlqIds,
      }),
    });

    const payload = await response.json();
    if (!response.ok) {
      const detail =
        typeof payload.detail === "string"
          ? payload.detail
          : JSON.stringify(payload.detail ?? payload);
      throw new Error(detail);
    }

    setSelectedDlqIds([]);
    await loadDlq();
  } catch (error) {
    console.error("Unable to replay selected dead letters:", error);
    setDlqError(
      error instanceof Error
        ? error.message
        : "Unable to request selected DLQ replay.",
    );
  } finally {
    setReplayingDeliveryId(null);
  }
};

const replayAllDeadLetters = async () => {
  setReplayingDeliveryId("__all__");
  setDlqError("");

  try {
    const response = await fetch(`${API_URL}/replay`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        endpoint_id: dashboardState.endpoint_id,
        limit: 50,
      }),
    });

    const payload = await response.json();

    if (!response.ok) {
      const detail =
        typeof payload.detail === "string"
          ? payload.detail
          : JSON.stringify(payload.detail ?? payload);

      throw new Error(detail);
    }

    await loadDlq();
  } catch (error) {
    console.error("Unable to replay dead letters:", error);
    setDlqError(
      error instanceof Error
        ? error.message
        : "Unable to request DLQ replay.",
    );
  } finally {
    setReplayingDeliveryId(null);
  }
};

useEffect(() => {
  const initialLoad = window.setTimeout(() => {
    void loadDlq();
  }, 0);

  const refreshTimer = window.setInterval(() => {
    void loadDlq();
  }, 5000);

  return () => {
    window.clearTimeout(initialLoad);
    window.clearInterval(refreshTimer);
  };
}, [loadDlq]);

useEffect(() => {
  if (dashboardState.source !== "live") {
    return undefined;
  }

  let cancelled = false;

  const loadSignatures = async () => {
    try {
      const response = await fetch(`${SIMULATOR_URL}/simulator/deliveries?limit=200`);

      if (!response.ok) {
        return;
      }

      const received = await response.json();

      if (cancelled || !Array.isArray(received)) {
        return;
      }

      setSignatures((previous) => {
        const next = { ...previous };
        for (const item of received) {
          if (item.attempt_id) {
            next[item.attempt_id] = item;
          }
        }
        return next;
      });
    } catch {
      // The badge is optional; the timeline still renders without it.
    }
  };

  void loadSignatures();
  const timer = window.setInterval(() => void loadSignatures(), 2000);

  return () => {
    cancelled = true;
    window.clearInterval(timer);
  };
}, [dashboardState.source]);

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

const requestRhythm = useMemo(() => {
  const times = (dashboardState.delivery_events ?? [])
    .map((event) => new Date(event.timestamp).getTime())
    .filter(Number.isFinite);
  if (times.length < 2) {
    return null;
  }
  const gaps = times.slice(0, -1).map((time, i) => Math.max(0, (time - times[i + 1]) / 1000));
  return {
    count: times.length,
    average: gaps.reduce((sum, gap) => sum + gap, 0) / gaps.length,
    longest: Math.max(...gaps),
  };
}, [dashboardState.delivery_events]);

const radarData = useMemo(() => {
  const scores = dashboardState.eri_sub_scores ?? {};
  const weights = dashboardState.eri_weights ?? {};

  const metrics = [
    { key: "success_rate", label: "Success" },
    { key: "latency", label: "Latency" },
    { key: "consecutive_failures", label: "Failure streak" },
    { key: "timeout_rate", label: "Timeouts" },
    { key: "http_5xx_rate", label: "HTTP 5xx" },
    { key: "queue_depth", label: "Queue depth" },
    { key: "recovery_trend", label: "Recovery trend" },
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

const thresholds = useMemo(
  () => dashboardState.thresholds ?? {},
  [dashboardState.thresholds],
);

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

      {activeTab === "dashboard" && (dashboardState.policy_tier ?? 1) > 1 && (
        <section className="fallback-banner" role="status">
          <div>
            <strong>
              {dashboardState.policy_tier === 2
                ? "⚠ Policy fallback: Tier 2 — cached policy"
                : "⚠ Policy fallback: Tier 3 — static defaults"}{" "}
              <HelpTip term="policy_tier" />
            </strong>
            <span>
              {dashboardState.policy_tier === 2
                ? "AEDE is unavailable. The Dispatcher keeps serving the last policy AEDE produced until it expires."
                : "AEDE is unavailable and the cached policy has expired. Delivery continues on static Strategy A defaults."}
            </span>
          </div>
        </section>
      )}

      {activeTab === "dashboard" && dashboardState.state === "QUARANTINED" && (
        <section className="quarantine-banner" role="alert">
          <div>
            <strong>🚨 Endpoint Quarantined <HelpTip term="restore" /></strong>
            <span>
              {dashboardState.endpoint_id ?? "Current endpoint"} is isolated.
              Relay has stopped sending to it and is holding its events. It will re-check the
              endpoint by itself after its quarantine hold; if you have fixed it (for example
              ▶ Normal), bring it back now.
            </span>
          </div>
          <button
            type="button"
            className="restore-button"
            onClick={() => void restoreEndpoint()}
          >
            Bring endpoint back
          </button>
        </section>
      )}

      {activeTab === "dashboard" && alerts.length > 0 && (
        <aside className="alert-stack" aria-live="assertive">
          {alerts.map((alert) => (
            <div className={`alert-toast ${alert.state === "QUARANTINED" ? "quarantine" : "unstable"}`} key={alert.id}>
              <button
                type="button"
                className="alert-toast-close"
                aria-label="Close"
                onClick={() => setAlerts((previous) => previous.filter((item) => item.id !== alert.id))}
              >
                ×
              </button>
              <div className="alert-toast-title">{alert.title}</div>
              <div>Endpoint {alert.endpointId} {alert.state === "QUARANTINED" ? "has been quarantined." : "is unstable."}</div>
              <div>ERI: {alert.eri} | Success rate: {alert.successRate}</div>
              {alert.message && <div className="alert-toast-message">{alert.message}</div>}
              <div className="alert-toast-actions">
                <button
                  type="button"
                  onClick={() => document.getElementById("policy-explainability")?.scrollIntoView({ behavior: "smooth", block: "start" })}
                >
                  View Explainability
                </button>
              </div>
            </div>
          ))}
          {alerts.length > 1 && (
            <button type="button" className="alert-dismiss-all" onClick={() => setAlerts([])}>
              Dismiss all
            </button>
          )}
        </aside>
      )}

      <div className="dashboard-tabs">
        <button
          type="button"
          className={`dashboard-tab ${
            activeTab === "dashboard" ? "active" : ""
          }`}
          onClick={() => setActiveTab("dashboard")}
        >
          Dashboard
        </button>

        <button
          type="button"
          className={`dashboard-tab ${
            activeTab === "benchmark" ? "active" : ""
          }`}
          onClick={() => setActiveTab("benchmark")}
        >
          Benchmark
        </button>

        <button
          type="button"
          className={`dashboard-tab ${
            activeTab === "multi-endpoint" ? "active" : ""
          }`}
          onClick={() => setActiveTab("multi-endpoint")}
        >
          Multi-Endpoint
        </button>
        <button
          type="button"
          className={`dashboard-tab ${activeTab === "lab" ? "active" : ""}`}
          onClick={() => setActiveTab("lab")}
        >
          Scenario Lab
        </button>
      </div>

      {activeTab === "dashboard" && (
        <section className="dashboard-grid">
        <article className="dashboard-card state-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                Endpoint State <HelpTip term="endpoint_state" />
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
            {dashboardState.state === "QUARANTINED" && (
              <button
                type="button"
                className="restore-button state-restore"
                onClick={() => void restoreEndpoint()}
              >
                Bring endpoint back
              </button>
            )}
          </div>
          {restoreNotice && dashboardState.state !== "QUARANTINED" && (
            <p className="restore-notice">{restoreNotice}</p>
          )}
          {STATE_BEHAVIOUR[dashboardState.state] && (
            <div className={`state-behaviour ${stateClass}`}>
              <p className="state-behaviour-summary">
                {STATE_BEHAVIOUR[dashboardState.state].summary}
              </p>
              {dashboardState.state !== "AVAILABLE" && (
                <p className="state-behaviour-heading">Compared with a healthy endpoint:</p>
              )}
              <ul>
                {STATE_BEHAVIOUR[dashboardState.state].points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            </div>
          )}
        </article>

        <article className="dashboard-card eri-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                Endpoint Reliability Index <HelpTip term="eri" />
              </h2>

              <p className="card-description">
                Current composite reliability score
              </p>
            </div>
          </div>

          <div className="eri-value">
            {formatEri(dashboardState.eri)}
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
                Live Stream <HelpTip term="live_stream" />
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
        Fault Injection <HelpTip term="fault_injection" />
      </h2>

      <p className="card-description">
        {dashboardState.source === "synthetic"
          ? "Configure the synthetic demo telemetry"
          : "Configure the endpoint simulator"}
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
        {FAULT_MODES.map((mode) => (
          <option key={mode.value} value={mode.value}>
            {mode.label}
          </option>
        ))}
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

    {faultError && <div className="dlq-error">{faultError}</div>}

<div className="scenario-presets">
  <p className="fault-label">Scenario presets <HelpTip term="scenario_presets" /></p>

  <div className="scenario-preset-buttons">
    {scenarioPresets.map((preset) => (
      <button
  key={preset.id}
  type="button"
  className={`scenario-preset-button ${
    activeScenario === preset.id ? "active" : ""
  }`}
  onClick={() => applyScenarioPreset(preset)}
  title={SCENARIO_GUIDE[preset.id]?.story}
>
  {preset.label}
</button>
    ))}
  </div>

  {dashboardState.source === "live" && (
    <div className="aede-switch">
      <span>
        Policy engine (AEDE) <HelpTip term="aede_outage" />:{" "}
        <strong>{aedeAvailable ? "available" : "simulated outage"}</strong>
      </span>
      <button
        type="button"
        className={aedeAvailable ? "aede-switch-stop" : "aede-switch-start"}
        onClick={() => void setAedeAvailability(!aedeAvailable)}
      >
        {aedeAvailable ? "Simulate AEDE outage" : "Bring AEDE back"}
      </button>
      {aedeSwitchError && <div className="dlq-error">{aedeSwitchError}</div>}
    </div>
  )}

  <p className="active-scenario" aria-live="polite">
    Active scenario:{" "}
    <strong>
      {scenarioPresets.find(
        (preset) => preset.id === activeScenario,
      )?.label ?? "None selected"}
    </strong>
  </p>

  {activeScenario && SCENARIO_GUIDE[activeScenario] && (
    <div className="scenario-guide">
      <p><strong>What's happening:</strong> {SCENARIO_GUIDE[activeScenario].story}</p>
      <p><strong>Watch for:</strong> {SCENARIO_GUIDE[activeScenario].watch}</p>
    </div>
  )}
</div>
  </div>
</article>

        <article className="dashboard-card chart-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                ERI History <HelpTip term="eri_history" />
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
      <ComposedChart
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
      </ComposedChart>
    </ResponsiveContainer>
  )}
</div>

<div className="eri-chart-legend">
  <HelpTip term="states" />
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
      label: "Success score",
      color: "#22c55e",
    },
    {
      key: "timeoutRate",
      label: "Timeout score",
      color: "#f97316",
    },
    {
      key: "consecutiveFailures",
      label: "Failure-streak score",
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
      <h2 className="card-title">ERI Sub-Metrics <HelpTip term="sub_metrics" /></h2>
      <p className="card-description">
        Health score per ERI factor (outer edge = healthy)
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
            isAnimationActive={false}
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
                Delivery Timeline <HelpTip term="delivery_timeline" />
              </h2>

              <p className="card-description">
                Recent delivery activity, newest first. The grey badge is the time since the
                previous attempt; amber means a long wait (backoff or isolation).
              </p>
              {requestRhythm && (
                <p className="timeline-rhythm">
                  Last {requestRhythm.count} attempts: one every{" "}
                  <b>{requestRhythm.average.toFixed(1)} s</b> on average, longest gap{" "}
                  <b>{requestRhythm.longest.toFixed(1)} s</b>
                </p>
              )}
            </div>
          </div>

          <div className="timeline">
            {dashboardState.delivery_events?.length === 0 ? (
              <div className="chart-empty">
                No delivery events available.
              </div>
            ) : (
              dashboardState.delivery_events.map((event, index, events) => (
  <div
    className="timeline-event"
    key={event.id}
  >
    <div className="timeline-main">
      <span className="timeline-time">
        {formatTime(event.timestamp)}
      </span>
      {(() => {
        // Rows are newest first, so the previous request is the next row.
        const older = events[index + 1];
        const gap = older
          ? (new Date(event.timestamp) - new Date(older.timestamp)) / 1000
          : null;
        if (gap == null || !Number.isFinite(gap)) {
          return null;
        }
        return (
          <span
            className={`timeline-gap ${gap >= 5 ? "long" : ""}`}
            title="Time since the previous delivery attempt to this endpoint"
          >
            +{gap < 10 ? gap.toFixed(1) : Math.round(gap)} s
          </span>
        );
      })()}

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

      {(() => {
        const received = signatures[event.attempt_id];
        const signature = event.signature ?? received?.signature;
        if (!signature) {
          return null;
        }
        const status = received?.signature_status;
        return (
          <span
            className={`signature-badge${status === "invalid" ? " signature-invalid" : ""}`}
            title={`X-Relay-Signature: ${signature}`}
          >
            {status === "verified" && "✓ verified · "}
            {status === "invalid" && "✗ invalid · "}
            X-Relay-Signature: {signature}
          </span>
        );
      })()}

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
                Policy Decision <HelpTip term="policy_decision" />
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
          ERI score <HelpTip term="eri" />
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.eri_score ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Max parallelism <HelpTip term="max_parallel" />
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.max_parallel ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Queue priority <HelpTip term="queue_priority" />
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.queue_priority ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Throughput limit <HelpTip term="throughput_limit" />
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.throughput_limit ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Max retries <HelpTip term="max_retries" />
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.max_retries ?? "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Backoff <HelpTip term="backoff" />
        </span>

        <span className="policy-value">
          {dashboardState.policy_decision.backoff_strategy || "--"}
        </span>
      </div>

      <div className="policy-row">
        <span className="policy-label">
          Retry delay <HelpTip term="retry_delay" />
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
          Isolation <HelpTip term="isolation" />
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

        <article id="policy-explainability" className="dashboard-card explanation-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                Policy Explainability <HelpTip term="explainability" />
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
            Strategy <HelpTip term="strategies" />
          </span>

          <span className="explanation-value">
            {dashboardState.policy_explanation.strategy_used ||
              "--"}
          </span>
        </div>

        <div className="explanation-item">
          <span className="explanation-label">
            Window size <HelpTip term="window_size" />
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
                <article className="dashboard-card dlq-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                Dead Letter Queue <HelpTip term="dlq" />
              </h2>

              <p className="card-description">
                Failed deliveries requiring operator action
              </p>
            </div>

            <div className="dlq-actions">
              <span className="dlq-count">
                {dlqItems.length} item{dlqItems.length === 1 ? "" : "s"}
              </span>

              <button
                className="fault-apply"
                type="button"
                onClick={() => void loadDlq()}
                disabled={dlqLoading}
              >
                {dlqLoading ? "Refreshing..." : "Refresh"}
              </button>

              {dlqItems.length > 0 && (
                <button
                  className="fault-apply"
                  type="button"
                  onClick={() => void replayAllDeadLetters()}
                  disabled={replayingDeliveryId !== null}
                >
                  {replayingDeliveryId === "__all__"
                    ? "Replaying..."
                    : "Replay All"}
                </button>
              )}

              {selectedDlqIds.length > 0 && (
                <button
                  className="fault-apply"
                  type="button"
                  onClick={() => void replaySelectedDeadLetters()}
                  disabled={replayingDeliveryId !== null}
                >
                  {replayingDeliveryId === "__selected__"
                    ? "Replaying..."
                    : `Replay Selected (${selectedDlqIds.length})`}
                </button>
              )}
            </div>
          </div>

          {dlqError && (
            <div className="dlq-error" role="alert">
              {dlqError}
            </div>
          )}

          {dlqLoading && dlqItems.length === 0 ? (
            <div className="chart-empty">
              Loading dead letters...
            </div>
          ) : dlqItems.length === 0 ? (
            <div className="chart-empty">
              No dead-letter deliveries.
            </div>
          ) : (
            <div className="dlq-table-wrapper">
              <table className="dlq-table">
                <thead>
                  <tr>
                    <th><input type="checkbox" aria-label="Select all dead letters" checked={dlqItems.length > 0 && selectedDlqIds.length === dlqItems.length} onChange={(event) => setSelectedDlqIds(event.target.checked ? dlqItems.map((item) => item.delivery_id) : [])} /></th>
                    <th>Delivery</th>
                    <th>Endpoint</th>
                    <th>Reason <HelpTip term="dlq_reason" /></th>
                    <th>Attempts</th>
                    <th>Last Error</th>
                    <th>Failed At</th>
                    <th>Action</th>
                  </tr>
                </thead>

                <tbody>
                  {dlqItems.map((item) => (
                    <tr key={item.delivery_id}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select delivery ${item.delivery_id}`}
                          checked={selectedDlqIds.includes(item.delivery_id)}
                          onChange={(event) => {
                            setSelectedDlqIds((previous) =>
                              event.target.checked
                                ? [...previous, item.delivery_id]
                                : previous.filter((id) => id !== item.delivery_id),
                            );
                          }}
                        />
                      </td>
                      <td>
                        <span className="policy-mono">
                          {item.delivery_id}
                        </span>
                      </td>

                      <td>
                        <span className="policy-mono">
                          {item.endpoint_id}
                        </span>
                      </td>

                      <td>
                        <span className="dlq-reason">
                          {item.reason}
                        </span>
                      </td>

                      <td>
                        {item.attempt_count}
                      </td>

                      <td>
                        <span className="dlq-error-text">
                          {item.last_error || "--"}
                        </span>
                      </td>

                      <td>
                        {formatTime(item.failed_at)}
                      </td>

                      <td>
                        <button
                          className="fault-apply"
                          type="button"
                          onClick={() =>
                            void replayDeadLetter(item.delivery_id)
                          }
                          disabled={
                            replayingDeliveryId !== null
                          }
                        >
                          {replayingDeliveryId === item.delivery_id
                            ? "Replaying..."
                            : "Replay"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </article>
        <article className="dashboard-card state-journal-card">
          <div className="card-header">
            <div>
              <h2 className="card-title">State Transition Journal <HelpTip term="journal" /></h2>
              <p className="card-description">
                State changes observed from the live policy stream
              </p>
            </div>

            <span className="dlq-count">
              {stateTransitions.length} transition
              {stateTransitions.length === 1 ? "" : "s"}
            </span>
          </div>

          {stateTransitions.length === 0 ? (
            <div className="chart-empty">
              No state transitions observed yet.
            </div>
          ) : (
            <div className="state-journal">
              {stateTransitions.map((transition) => (
                <div
                  className="state-journal-entry"
                  key={transition.id}
                >
                  <div className="state-journal-time">
                    {formatTime(transition.evaluatedAt)}
                  </div>

                  <div className="state-journal-transition">
                    <span
                      className={`state-journal-state ${getStateClass(
                        transition.stateBefore,
                      )}`}
                    >
                      {transition.stateBefore ?? "--"}
                    </span>
                    <span className="state-journal-arrow">→</span>
                    <span
                      className={`state-journal-state ${getStateClass(
                        transition.stateAfter,
                      )}`}
                    >
                      {transition.stateAfter ?? "--"}
                    </span>
                  </div>

                  <div className="state-journal-details">
                    <div>
                      <strong>ERI:</strong> {transition.eri}
                    </div>
                    <div>
                      <strong>Strategy:</strong>{" "}
                      {transition.strategyUsed}
                    </div>
                    <div>
                      <strong>Reason:</strong>{" "}
                      {transition.reason}
                    </div>
                    {transition.triggeredThresholds.length > 0 && (
                      <div>
                        <strong>Thresholds:</strong>{" "}
                        {transition.triggeredThresholds.join(", ")}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </article>
      </section>
      )}

      {activeTab === "multi-endpoint" && <NoisyNeighbour />}

      {activeTab === "benchmark" && <BenchmarkTab />}
      {activeTab === "lab" && <ScenarioLab />}
    </main>
  );
}

export default App;
