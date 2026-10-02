// Entry point: serve `handle` over A2A (JSON-RPC and the agent card). Binds 127.0.0.1 by default:
// the chassis reaches it on localhost in the `sidecar` lane. HOST and PORT override; a HOST that is
// not loopback is refused unless ALLOW_ANY_HOST=1 (ADR-001, `bindHost`). With UDS set to a path, it
// listens on that Unix socket instead and binds no TCP port, so the loopback guard does not apply;
// a stale socket file at the path is removed first. The agent card still names
// http://127.0.0.1:<PORT>, which a client over the socket only uses for the Host header. No key is
// read here or anywhere in this workload; the model URL (CHASSIS_MODEL_URL) is the chassis proxy.
// On SIGTERM it drains (`drainOnSignals`): in-flight calls finish, then exit 0; a call that outlives
// DRAIN_TIMEOUT_MS (suggested default 30000) forces exit 1. A bad DRAIN_TIMEOUT_MS is refused at
// start (exit 2).

import { lstatSync, rmSync } from "node:fs";

import { buildAgentCard, buildApp } from "./a2a_server.js";
import { drainOnSignals, drainTimeoutMs } from "./drain.js";
import { handle } from "./handle.js";
import { bindHost } from "./host.js";

const uds = process.env["UDS"] || undefined;
let host: string;
let drainMs: number;
try {
  host = uds ? "127.0.0.1" : bindHost(process.env);
  drainMs = drainTimeoutMs(process.env);
} catch (err) {
  console.error(`echo-typescript: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(2);
}
const port = Number(process.env["PORT"] ?? "9000"); // suggested: 9000
const card = buildAgentCard({ name: "echo-typescript", version: "0.1.0", url: `http://${host}:${port}` });
const app = buildApp(handle, card);

function removeStaleSocket(path: string): void {
  try {
    if (!lstatSync(path).isSocket()) throw new Error(`UDS=${path} exists and is not a socket`);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return;
    console.error(`echo-typescript: ${err instanceof Error ? err.message : String(err)}`);
    process.exit(2);
  }
  rmSync(path);
}

let server: ReturnType<typeof app.listen>;
if (uds) {
  removeStaleSocket(uds);
  server = app.listen(uds, () => {
    console.log(`echo-typescript: A2A on unix:${uds} (card: /.well-known/agent-card.json)`);
  });
} else {
  server = app.listen(port, host, () => {
    console.log(`echo-typescript: A2A on http://${host}:${port}/ (card: /.well-known/agent-card.json)`);
  });
}
drainOnSignals(server, drainMs);
