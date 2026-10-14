import { useEffect, useMemo, useState } from "react";

import { DASHBOARD_URL } from "./config";
import HelpTip from "./HelpTip";

// Replays the noisy-neighbour simulation: two customers' endpoints share one
// pool of delivery workers, one endpoint fails, and we watch whether the
// healthy one notices. All three strategies run on one shared clock.

const STRATEGIES = [
  { key: "STRATEGY_A", short: "A", name: "Fixed retries", color: "#dc2626" },
  { key: "STRATEGY_B", short: "B", name: "Adaptive backoff", color: "#2563eb" },
  { key: "STRATEGY_C", short: "C", name: "AEDE", color: "#16a34a" },
];

const SPEEDS = [2, 5, 10, 20];
const FRAME_MS = 50;
const QUEUE_CAP = 40;

// Job fields, as sent by GET /api/chart/benchmark/multi-endpoint/trace.
const ENDPOINT = 0;
const EVENT = 1;
const ARRIVAL = 2;
const START = 3;
const END = 4;
const WORKER = 5;
const RETRIES = 7;
const FAILING = 0;
const HEALTHY = 1;

function snapshot(trace, t, poolSize) {
  const workers = Array.from({ length: poolSize }, () => null);
  const waiting = { [FAILING]: 0, [HEALTHY]: 0 };
  let healthyDone = 0;
  let healthyWaitSum = 0;
  let failingRetries = 0;
  let oldestHealthyWait = 0;

  for (const job of trace.jobs) {
    if (job[ARRIVAL] > t) continue;
    const isolated = job[WORKER] < 0;
    if (isolated) continue;
    if (job[START] > t) {
      waiting[job[ENDPOINT]] += 1;
      if (job[ENDPOINT] === HEALTHY) {
        oldestHealthyWait = Math.max(oldestHealthyWait, t - job[ARRIVAL]);
      }
    } else if (job[END] > t) {
      workers[job[WORKER]] = job;
    }
    if (job[ENDPOINT] === HEALTHY && job[END] <= t) {
      healthyDone += 1;
      healthyWaitSum += job[START] - job[ARRIVAL];
    }
    if (job[ENDPOINT] === FAILING && job[START] <= t) {
      failingRetries += job[RETRIES];
    }
  }

  const quarantined =
    trace.quarantined_from_event != null &&
    trace.jobs.some((job) => job[ENDPOINT] === FAILING && job[EVENT] === trace.quarantined_from_event && job[ARRIVAL] <= t);

  return {
    workers,
    waiting,
    healthyDone,
    healthyAvgWait: healthyDone ? healthyWaitSum / healthyDone : 0,
    oldestHealthyWait,
    failingRetries,
    quarantined,
    finished: t >= trace.total_sim_time_sec * 1000,
  };
}

function Queue({ count, kind }) {
  const shown = Math.min(count, QUEUE_CAP);
  return (
    <div className="nn-queue">
      {Array.from({ length: shown }, (_, i) => (
        <span key={i} className={`nn-dot ${kind}`} />
      ))}
      {count > QUEUE_CAP && <span className="nn-more">+{count - QUEUE_CAP}</span>}
      {count === 0 && <span className="nn-empty">none waiting</span>}
    </div>
  );
}

const seconds = (ms) => `${(ms / 1000).toFixed(1)} s`;

export default function NoisyNeighbour() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(10);

  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch(`${DASHBOARD_URL}/api/chart/benchmark/multi-endpoint/trace`);
        if (!response.ok) throw new Error(`Unable to load the simulation (${response.status})`);
        setData(await response.json());
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to load the simulation.");
      }
    })();
  }, []);

  const endMs = useMemo(
    () => (data ? Math.max(...data.strategies.map((s) => s.total_sim_time_sec)) * 1000 : 0),
    [data],
  );

  useEffect(() => {
    if (!playing) return undefined;
    const timer = window.setInterval(() => {
      setT((current) => {
        const next = current + FRAME_MS * speed;
        if (next >= endMs) {
          setPlaying(false);
          return endMs;
        }
        return next;
      });
    }, FRAME_MS);
    return () => window.clearInterval(timer);
  }, [playing, speed, endMs]);

  const byKey = useMemo(() => {
    const map = {};
    for (const s of data?.strategies ?? []) map[s.strategy] = s;
    return map;
  }, [data]);

  const outageAt = data ? data.failing_from_event * data.arrival_interval_ms : 0;
  const arrivalsEnd = data ? data.events_per_endpoint * data.arrival_interval_ms : 0;
  const finished = data && t >= endMs;

  return (
    <section className="nn-page">
      <article className="dashboard-card">
        <h2 className="card-title">
          Noisy neighbour: does one customer's outage slow down everyone else?{" "}
          <HelpTip term="multi_endpoint" />
        </h2>
        <div className="nn-explainer">
          <p>
            Relay delivers for many customers through <b>one shared pool of delivery workers</b>{" "}
            (4 here). Two shops each send 200 events, one every 50 ms:
          </p>
          <ul>
            <li>
              <span className="nn-dot failing" /> <b>Shop A</b> goes down after its 40th event.
              Every request to it now hangs for 1.5 s before timing out, holding a worker the whole
              time, and a retrying strategy sends it again.
            </li>
            <li>
              <span className="nn-dot healthy" /> <b>Shop B</b> is perfectly healthy: each delivery
              takes 35 ms.
            </li>
          </ul>
          <p>
            Shop B has done nothing wrong. The question is whether Shop B's events end up stuck in
            line behind Shop A's doomed retries. Press play: the three strategies run side by side on
            the same clock.
          </p>
        </div>
        {error && <div className="dlq-error">{error}</div>}
      </article>

      {data && (
        <>
          <article className="dashboard-card">
            <div className="lab-controls">
              <button
                type="button"
                className="lab-play"
                onClick={() => {
                  if (finished) setT(0);
                  setPlaying((value) => !value);
                }}
              >
                {playing ? "⏸ Pause" : finished ? "↻ Play again" : t > 0 ? "▶ Resume" : "▶ Play"}
              </button>
              <button type="button" className="lab-secondary" onClick={() => { setPlaying(false); setT(0); }}>
                Reset
              </button>
              <button type="button" className="lab-secondary" onClick={() => { setPlaying(false); setT(endMs); }}>
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
                Simulated time {seconds(t)}
                <div className="lab-progress-bar">
                  <div style={{ width: `${endMs ? (t / endMs) * 100 : 0}%` }} />
                </div>
              </div>
            </div>
            <div className="lab-now">
              {t < outageAt
                ? "Both shops are healthy. Every event is picked up as soon as it arrives."
                : t < arrivalsEnd
                  ? "Shop A is down. Watch what happens to Shop B's queue under each strategy."
                  : "All events have arrived. The strategies are working through whatever is still queued."}
            </div>
          </article>

          <div className="nn-columns">
            {STRATEGIES.map((s) => {
              const trace = byKey[s.key];
              const snap = snapshot(trace, t, data.worker_pool_size);
              return (
                <article key={s.key} className="dashboard-card nn-column" style={{ borderTopColor: s.color }}>
                  <h3>
                    <span style={{ color: s.color }}>{s.short}</span> · {s.name}
                  </h3>
                  <div className={`nn-status ${snap.quarantined ? "isolated" : ""}`}>
                    {s.key === "STRATEGY_C"
                      ? snap.quarantined
                        ? "Shop A is quarantined: it no longer gets any workers."
                        : "Watching Shop A's health."
                      : "No health model: keeps sending to Shop A."}
                  </div>

                  <p className="nn-label">Waiting for a worker</p>
                  <div className="nn-row">
                    <span className="nn-who">Shop A</span>
                    <Queue count={snap.waiting[FAILING]} kind="failing" />
                  </div>
                  <div className="nn-row">
                    <span className="nn-who">Shop B</span>
                    <Queue count={snap.waiting[HEALTHY]} kind="healthy" />
                  </div>

                  <p className="nn-label">Shared workers</p>
                  <div className="nn-workers">
                    {snap.workers.map((job, i) => (
                      <div
                        key={i}
                        className={`nn-worker ${job ? (job[ENDPOINT] === FAILING ? "failing" : "healthy") : ""}`}
                      >
                        {job ? (job[ENDPOINT] === FAILING ? "Shop A · timing out" : "Shop B · ✓") : "idle"}
                      </div>
                    ))}
                  </div>

                  <div className="lab-lane-stats nn-stats">
                    <div>
                      <b>{snap.healthyDone}/{data.events_per_endpoint}</b>
                      <span>Shop B delivered</span>
                    </div>
                    <div>
                      <b>{seconds(snap.oldestHealthyWait)}</b>
                      <span>Shop B's oldest wait now</span>
                    </div>
                    <div>
                      <b>{snap.failingRetries.toLocaleString()}</b>
                      <span>retries at Shop A</span>
                    </div>
                  </div>
                  {snap.healthyDone === data.events_per_endpoint && (
                    <p className="nn-done">
                      Shop B fully delivered by {seconds(Math.max(...trace.jobs.filter((j) => j[ENDPOINT] === HEALTHY).map((j) => j[END])))}{" "}
                      (average wait {seconds(snap.healthyAvgWait)})
                    </p>
                  )}
                </article>
              );
            })}
          </div>

          {finished && (
            <article className="dashboard-card lab-summary">
              <h3>What happened</h3>
              <p>
                Shop B sent the same 200 events in every run. Under fixed retries, Shop A's timeouts
                and retries held the workers, so Shop B's events queued for{" "}
                <b>{seconds(snapshot(byKey.STRATEGY_A, endMs, data.worker_pool_size).healthyAvgWait)}</b> on
                average. Under AEDE, Shop A was quarantined after a few failures, its requests stopped
                taking workers, and Shop B waited{" "}
                <b>{seconds(snapshot(byKey.STRATEGY_C, endMs, data.worker_pool_size).healthyAvgWait)}</b>.
              </p>
              <p className="lab-caveat">
                Isolation protects the customers who did nothing wrong. Shop A's events are not lost:
                they are held and can be replayed once Shop A is back.
              </p>
            </article>
          )}
        </>
      )}
    </section>
  );
}
