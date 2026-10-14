import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const DASHBOARD_API = "http://127.0.0.1:8000";

const SCENARIO_IDS = [3, 4, 5, 9, 10, 12, 13, 14];

const STRATEGIES = [
  { key: "STRATEGY_A", label: "Strategy A" },
  { key: "STRATEGY_B", label: "Strategy B" },
  { key: "STRATEGY_C", label: "Strategy C (AEDE)" },
];

const METRICS = {
  wasted_retries: {
    label: "Wasted retries",
    value: (run) => run.total_wasted_retries,
    format: (value) => Number(value).toLocaleString(),
    axisLabel: "Wasted retries",
  },
  retry_count: {
    label: "Retry count",
    value: (run) => run.avg_retry_count,
    format: (value) => Number(value).toFixed(2),
    axisLabel: "Average retries / event",
  },
  success_rate: {
    label: "Success rate",
    value: (run) => (
      run.delivery_success_rate == null
        ? null
        : run.delivery_success_rate * 100
    ),
    format: (value) => `${Number(value).toFixed(1)}%`,
    axisLabel: "Success rate (%)",
  },
  mttr: {
    label: "MTTR",
    value: (run) => run.avg_recovery_time_seconds,
    format: (value) => `${Number(value).toFixed(1)} s`,
    axisLabel: "Average recovery time (s)",
  },
};

function strategyLabel(strategy) {
  return (
    STRATEGIES.find((item) => item.key === strategy)?.label ?? strategy
  );
}

function formatMetric(value, metricKey) {
  if (value == null || Number.isNaN(Number(value))) {
    return "—";
  }

  return METRICS[metricKey].format(value);
}

function formatComparison(metric, metricKey) {
  if (!metric) {
    return "—";
  }

  if (metricKey === "success_rate") {
    const points = Number(metric.pct_change ?? 0);
    return `${points >= 0 ? "+" : ""}${points.toFixed(2)} percentage points`;
  }

  const pct = Number(metric.pct_change ?? 0);
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function comparisonDescription(metric, metricKey) {
  if (!metric) {
    return "Run all three strategies to calculate comparisons.";
  }

  if (metricKey === "success_rate") {
    const points = Number(metric.pct_change ?? 0);
    return `${points >= 0 ? "Higher" : "Lower"} by ${Math.abs(points).toFixed(
      2,
    )} percentage points`;
  }

  const pct = Number(metric.pct_change ?? 0);
  return `${pct < 0 ? "Reduction" : "Increase"} of ${Math.abs(pct).toFixed(
    1,
  )}%`;
}

export default function BenchmarkTab() {
  const [runs, setRuns] = useState([]);
  const [comparison, setComparison] = useState({});
  const [metricKey, setMetricKey] = useState("wasted_retries");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
  let cancelled = false;

  const fetchResults = async () => {
    try {
      const response = await fetch(
        `${DASHBOARD_API}/api/chart/benchmark/results`,
      );

      if (!response.ok) {
        throw new Error(
          `Unable to load benchmark results (${response.status})`,
        );
      }

      const payload = await response.json();

      if (!cancelled) {
        setRuns(Array.isArray(payload.runs) ? payload.runs : []);
      }
    } catch (err) {
      if (!cancelled) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load benchmark results.",
        );
      }
    }
  };

  void fetchResults();

  return () => {
    cancelled = true;
  };
}, []);

  async function runStrategies(strategies) {
    setLoading(true);
    setError("");
    setMessage("");

    try {
      const response = await fetch(
        `${DASHBOARD_API}/api/chart/benchmark/run`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            scenario_ids: SCENARIO_IDS,
            strategies,
            seed: 42,
          }),
        },
      );

      const payload = await response.json();

      if (!response.ok) {
        const detail =
          typeof payload.detail === "string"
            ? payload.detail
            : JSON.stringify(payload.detail ?? payload);
        throw new Error(detail);
      }

      setRuns(Array.isArray(payload.runs) ? payload.runs : []);
      setComparison(payload.comparison ?? {});

      const count = Array.isArray(payload.runs) ? payload.runs.length : 0;
      setMessage(`Completed ${count} benchmark runs.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Benchmark request failed.");
    } finally {
      setLoading(false);
    }
  }

  async function runAll() {
    await runStrategies(STRATEGIES.map((strategy) => strategy.key));
  }

  const chartData = useMemo(() => {
    const byScenario = new Map();

    for (const scenarioId of SCENARIO_IDS) {
      byScenario.set(scenarioId, {
        scenario: String(scenarioId),
        "Strategy A": null,
        "Strategy B": null,
        "Strategy C (AEDE)": null,
      });
    }

    for (const run of runs) {
      if (!SCENARIO_IDS.includes(run.scenario_id)) {
        continue;
      }

      const strategy = strategyLabel(run.strategy);
      const metric = METRICS[metricKey];
      const value = metric.value(run);

      const row = byScenario.get(run.scenario_id);
      if (row) {
        row[strategy] = value;
      }
    }

    return Array.from(byScenario.values());
  }, [runs, metricKey]);

  const latestRunCount = runs.length;

  const cVsA = comparison.c_vs_a;
  const cVsB = comparison.c_vs_b;

  const headlineMetric = metricKey === "wasted_retries"
    ? "wasted_retries"
    : "wasted_retries";

  const kpiText = (comparisonItem) =>
    comparisonDescription(comparisonItem?.[headlineMetric], "wasted_retries");

  return (
    <section className="benchmark-page">
      <style>{`
        .benchmark-page {
          display: grid;
          gap: 20px;
        }

        .benchmark-card {
          min-width: 0;
          padding: 22px;
          border: 1px solid var(--border);
          border-radius: 14px;
          background: var(--surface);
          box-shadow: var(--shadow);
        }

        .benchmark-card h2 {
          margin: 0;
          color: var(--text-primary);
          font-size: 20px;
          font-weight: 700;
        }

        .benchmark-card p {
          color: var(--text-secondary);
        }

        .benchmark-description {
          margin: 6px 0 14px;
          font-size: 15px;
        }

        .benchmark-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .benchmark-button {
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 9px 14px;
          background: var(--surface);
          color: var(--text-primary);
          cursor: pointer;
          font-size: 14px;
          font-weight: 600;
        }

        .benchmark-button:hover:not(:disabled) {
          background: var(--background);
        }

        .benchmark-button:disabled {
          cursor: wait;
          opacity: 0.6;
        }

        .benchmark-status {
          margin: 14px 0 0;
          font-size: 14px;
          font-weight: 500;
        }

        .benchmark-error {
          margin: 14px 0 0;
          padding: 10px 12px;
          border: 1px solid #fecaca;
          border-radius: 8px;
          background: #fef2f2;
          color: #991b1b;
          font-size: 14px;
          white-space: pre-wrap;
        }

        .benchmark-kpi-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 20px;
        }

        .benchmark-kpi {
          min-width: 0;
        }

        .benchmark-kpi h2 {
          margin-bottom: 16px;
        }

        .benchmark-kpi-row {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
        }

        .benchmark-stat {
          padding: 14px;
          border: 1px solid var(--border);
          border-radius: 10px;
          background: var(--background);
        }

        .benchmark-stat-label {
          margin: 0 0 6px;
          color: var(--text-secondary);
          font-size: 13px;
        }

        .benchmark-stat-value {
          margin: 0;
          color: var(--text-primary);
          font-size: 20px;
          font-weight: 700;
        }

        .benchmark-stat-note {
          margin: 5px 0 0;
          color: var(--text-secondary);
          font-size: 12px;
        }

        .benchmark-chart-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
          margin-bottom: 14px;
        }

        .benchmark-metric-control {
          display: flex;
          align-items: center;
          gap: 8px;
          color: var(--text-primary);
          font-size: 14px;
          white-space: nowrap;
        }

        .benchmark-metric-control select {
          min-width: 190px;
          padding: 7px 9px;
          border: 1px solid var(--border);
          border-radius: 8px;
          background: var(--surface);
          color: var(--text-primary);
          font: inherit;
        }

        .benchmark-chart {
          width: 100%;
          height: 430px;
          min-height: 430px;
        }

        .benchmark-empty {
          display: flex;
          align-items: center;
          justify-content: center;
          min-height: 100px;
          border: 1px dashed var(--border);
          border-radius: 10px;
          color: var(--text-secondary);
          text-align: center;
        }

        .benchmark-table-wrap {
          overflow-x: auto;
        }

        .benchmark-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 14px;
        }

        .benchmark-table th,
        .benchmark-table td {
          padding: 10px 8px;
          border-bottom: 1px solid var(--border);
          text-align: left;
          white-space: nowrap;
        }

        .benchmark-table th {
          color: var(--text-primary);
          font-weight: 700;
        }

        .benchmark-table td {
          color: var(--text-primary);
        }

        @media (max-width: 800px) {
          .benchmark-kpi-grid {
            grid-template-columns: 1fr;
          }

          .benchmark-chart-header {
            flex-direction: column;
          }

          .benchmark-metric-control {
            width: 100%;
          }

          .benchmark-metric-control select {
            flex: 1;
          }
        }
      `}</style>

      <article className="benchmark-card">
        <h2>Strategy Benchmark</h2>
        <p className="benchmark-description">
          Compare static retries, adaptive retries, and the full AEDE policy
          on the eight held-out evaluation scenarios.
        </p>

        <div className="benchmark-actions">
          {STRATEGIES.map((strategy) => (
            <button
              key={strategy.key}
              type="button"
              className="benchmark-button"
              disabled={loading}
              onClick={() => runStrategies([strategy.key])}
            >
              ▶ Run {strategy.label}
            </button>
          ))}

          <button
            type="button"
            className="benchmark-button"
            disabled={loading}
            onClick={runAll}
          >
            {loading ? "Running…" : "Run all strategies"}
          </button>
        </div>

        {message && (
          <p className="benchmark-status" aria-live="polite">
            {message}
          </p>
        )}

        {error && (
          <div className="benchmark-error" role="alert">
            {error}
          </div>
        )}
      </article>

      <div className="benchmark-kpi-grid">
        <article className="benchmark-card benchmark-kpi">
          <h2>Strategy C vs Strategy A</h2>

          <div className="benchmark-kpi-row">
            <div className="benchmark-stat">
              <p className="benchmark-stat-label">Wasted retries</p>
              <p className="benchmark-stat-value">
                {kpiText(cVsA)}
              </p>
              <p className="benchmark-stat-note">
                C: {formatMetric(cVsA?.wasted_retries?.strategy_c, "wasted_retries")}
                {" · "}
                A: {formatMetric(cVsA?.wasted_retries?.baseline, "wasted_retries")}
              </p>
            </div>

            <div className="benchmark-stat">
              <p className="benchmark-stat-label">Success rate</p>
              <p className="benchmark-stat-value">
                {formatComparison(cVsA?.success_rate, "success_rate")}
              </p>
              <p className="benchmark-stat-note">
                Mean across shared scenarios
              </p>
            </div>
          </div>

          <p className="benchmark-stat-note" style={{ marginTop: 14 }}>
            {cVsA?.scenarios?.length
              ? `Computed from scenarios: ${cVsA.scenarios.join(", ")}`
              : "Run all three strategies to calculate comparisons."}
          </p>
        </article>

        <article className="benchmark-card benchmark-kpi">
          <h2>Strategy C vs Strategy B</h2>

          <div className="benchmark-kpi-row">
            <div className="benchmark-stat">
              <p className="benchmark-stat-label">Wasted retries</p>
              <p className="benchmark-stat-value">
                {kpiText(cVsB)}
              </p>
              <p className="benchmark-stat-note">
                C: {formatMetric(cVsB?.wasted_retries?.strategy_c, "wasted_retries")}
                {" · "}
                B: {formatMetric(cVsB?.wasted_retries?.baseline, "wasted_retries")}
              </p>
            </div>

            <div className="benchmark-stat">
              <p className="benchmark-stat-label">Success rate</p>
              <p className="benchmark-stat-value">
                {formatComparison(cVsB?.success_rate, "success_rate")}
              </p>
              <p className="benchmark-stat-note">
                Mean across shared scenarios
              </p>
            </div>
          </div>

          <p className="benchmark-stat-note" style={{ marginTop: 14 }}>
            {cVsB?.scenarios?.length
              ? `Computed from scenarios: ${cVsB.scenarios.join(", ")}`
              : "Run all three strategies to calculate comparisons."}
          </p>
        </article>
      </div>

      <article className="benchmark-card">
        <div className="benchmark-chart-header">
          <div>
            <h2>Held-out Scenario Comparison</h2>
            <p className="benchmark-description">
              Results use the backend response for seed 42. Missing runs are
              shown as gaps, not fabricated values.
            </p>
          </div>

          <label className="benchmark-metric-control">
            Metric
            <select
              value={metricKey}
              onChange={(event) => setMetricKey(event.target.value)}
            >
              {Object.entries(METRICS).map(([key, metric]) => (
                <option key={key} value={key}>
                  {metric.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {runs.length === 0 ? (
          <div className="benchmark-empty">
            No benchmark results yet. Choose a strategy above to begin.
          </div>
        ) : (
          <div className="benchmark-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={chartData}
                margin={{ top: 10, right: 20, left: 10, bottom: 10 }}
                barGap={4}
                barCategoryGap="18%"
              >
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="scenario"
                  label={{
                    value: "Held-out scenario",
                    position: "insideBottom",
                    offset: -5,
                  }}
                />
                <YAxis
                  label={{
                    value: METRICS[metricKey].axisLabel,
                    angle: -90,
                    position: "insideLeft",
                  }}
                />
                <Tooltip
                  formatter={(value) => formatMetric(value, metricKey)}
                  labelFormatter={(label) => `Scenario ${label}`}
                />
                <Legend />
                <Bar dataKey="Strategy A" name="Strategy A" fill="#2563eb" />
                <Bar dataKey="Strategy B" name="Strategy B" fill="#f59e0b" />
                <Bar dataKey="Strategy C (AEDE)" name="Strategy C (AEDE)" fill="#16a34a" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </article>

      <article className="benchmark-card">
        <h2>Recorded Benchmark Runs</h2>
        <p className="benchmark-description">
          {latestRunCount
            ? `${latestRunCount} benchmark runs currently loaded.`
            : "Run a benchmark to populate this table."}
        </p>

        {runs.length === 0 ? (
          <div className="benchmark-empty">
            No recorded benchmark runs.
          </div>
        ) : (
          <div className="benchmark-table-wrap">
            <table className="benchmark-table">
              <thead>
                <tr>
                  <th>Scenario</th>
                  <th>Strategy</th>
                  <th>Success rate</th>
                  <th>Wasted retries</th>
                  <th>Avg. retries</th>
                </tr>
              </thead>
              <tbody>
                {[...runs]
                  .sort(
                    (a, b) =>
                      (a.scenario_id ?? 0) - (b.scenario_id ?? 0) ||
                      a.strategy.localeCompare(b.strategy),
                  )
                  .map((run) => (
                    <tr key={`${run.scenario_id}-${run.strategy}-${run.seed}`}>
                      <td>{run.scenario_id ?? "—"}</td>
                      <td>{strategyLabel(run.strategy)}</td>
                      <td>
                        {formatMetric(run.delivery_success_rate * 100, "success_rate")}
                      </td>
                      <td>{formatMetric(run.total_wasted_retries, "wasted_retries")}</td>
                      <td>{formatMetric(run.avg_retry_count, "retry_count")}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </article>
    </section>
  );
}
