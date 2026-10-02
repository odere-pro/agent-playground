// Drain on SIGTERM (PoC-4 section 6). The first SIGTERM or SIGINT stops accepting, closes idle
// keep-alive connections (the chassis holds one open, and `server.close()` alone would wait on
// it), lets in-flight `handle` calls finish, and exits 0 when the last connection ends. A call that
// outlives DRAIN_TIMEOUT_MS forces exit 1. A second signal exits 1 at once.

import type { Server } from "node:http";

export const DEFAULT_DRAIN_TIMEOUT_MS = 30_000; // suggested: 30000, the default budget.timeout_ms

const IDLE_SWEEP_MS = 100; // suggested

/** DRAIN_TIMEOUT_MS from the environment, or the default. Throws on a value that is not a
 * whole number of milliseconds, 0 or more. */
export function drainTimeoutMs(env: Record<string, string | undefined>): number {
  const raw = env["DRAIN_TIMEOUT_MS"];
  if (raw === undefined || raw === "") return DEFAULT_DRAIN_TIMEOUT_MS;
  const ms = Number(raw);
  if (!Number.isInteger(ms) || ms < 0) {
    throw new Error(`DRAIN_TIMEOUT_MS must be a whole number of milliseconds, 0 or more; got ${JSON.stringify(raw)}`);
  }
  return ms;
}

/** Stop accepting, close idle connections, and call `exit(0)` once the in-flight calls end, or
 * `exit(1)` after `timeoutMs`. Connections that go idle later are closed as they do. */
export function drain(server: Server, timeoutMs: number, exit: (code: number) => void): void {
  let done = false;
  const finish = (code: number): void => {
    if (done) return;
    done = true;
    exit(code);
  };
  // A keep-alive connection whose call ends after close() would stay open until it idles out
  // (keepAliveTimeout). Sweep idle connections every IDLE_SWEEP_MS so the exit follows the last
  // call closely.
  const sweep = setInterval(() => server.closeIdleConnections(), IDLE_SWEEP_MS);
  sweep.unref();
  const deadline = setTimeout(() => finish(1), timeoutMs);
  deadline.unref();
  server.close(() => {
    clearInterval(sweep);
    clearTimeout(deadline);
    finish(0);
  });
  server.closeIdleConnections();
}

/** Drain on the first SIGTERM or SIGINT; a second one calls `exit(1)` at once. Returns a function
 * that removes the listeners (for tests). */
export function drainOnSignals(
  server: Server,
  timeoutMs: number,
  exit: (code: number) => void = (code) => process.exit(code),
): () => void {
  let draining = false;
  const onSignal = (): void => {
    if (draining) return exit(1);
    draining = true;
    drain(server, timeoutMs, exit);
  };
  const signals = ["SIGTERM", "SIGINT"] as const;
  for (const signal of signals) process.on(signal, onSignal);
  return () => {
    for (const signal of signals) process.off(signal, onSignal);
  };
}
