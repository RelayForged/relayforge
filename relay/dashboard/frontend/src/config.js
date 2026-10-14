// Where each backend lives. The dashboard backend (chart API + WebSocket)
// serves this UI in compose, so in a build it is the page's own origin.
// Under `npm run dev` the page is on Vite's port, so it points at 8090.
// The ingestion API and the simulator are separate services on fixed ports
// of the same host. Any of them can be overridden with a VITE_* variable.

const host = window.location.hostname || "127.0.0.1";

function sameHost(port) {
  return `${window.location.protocol}//${host}:${port}`;
}

export const DASHBOARD_URL =
  import.meta.env.VITE_DASHBOARD_URL ??
  (import.meta.env.DEV ? "http://127.0.0.1:8090" : window.location.origin);

export const API_URL = import.meta.env.VITE_API_URL ?? sameHost(8000);

export const SIMULATOR_URL =
  import.meta.env.VITE_SIMULATOR_URL ?? sameHost(8088);

export const DASHBOARD_WS_URL = `${DASHBOARD_URL.replace(/^http/, "ws")}/ws`;

// Fault Injection options. `simulator` is the simulator's FailureMode
// (simulator/config.py); `synthetic` is the synthetic source's name for the
// same fault, or null where it has no equivalent.
export const FAULT_MODES = [
  { value: "mixed", label: "Mixed (500s and timeouts)", simulator: "mixed", synthetic: "mixed" },
  { value: "http_error", label: "HTTP 500", simulator: "http_error", synthetic: "http_500" },
  { value: "timeout", label: "Timeout", simulator: "timeout", synthetic: "timeout" },
  { value: "rate_limit", label: "HTTP 429 rate limit", simulator: "rate_limit", synthetic: null },
  { value: "auth_failure", label: "HTTP 401 auth failure", simulator: "auth_failure", synthetic: null },
  { value: "connection_reset", label: "Connection reset", simulator: "connection_reset", synthetic: null },
  { value: "slow", label: "Slow (latency only)", simulator: "slow", synthetic: null },
  { value: "success", label: "Healthy", simulator: "success", synthetic: null },
];
