// The workload's log lines (PoC-8, 014 H-7): one JSON object per line, with the field names of
// the chassis's `chassis.logs` and of `workload_a2a.logs`: `time` (UTC, ISO 8601, milliseconds,
// `Z`), `level`, `logger`, `message`, `agent`, then `request_id` and `trace_id` (32 lowercase
// hex) inside an A2A request, then the line's own fields. `HandleExecutor.execute` runs `handle`
// inside `withRequest` (AsyncLocalStorage), so a line logged anywhere in it carries the ids.
// `request_id` is `ctx.request_id`; `trace_id` is `ctx.traceparent`'s, else `ctx.trace_id` (the
// server never reads the HTTP header, see CLAUDE.md). No redaction here: the OTel Collector's log
// pipeline redacts workload logs (ADR-007 item 7). Never log a token.

import { AsyncLocalStorage } from "node:async_hooks";
import { createHash } from "node:crypto";

export type Level = "debug" | "info" | "warning" | "error";
export interface RequestIds {
  request_id?: string;
  trace_id?: string;
}

const current = new AsyncLocalStorage<RequestIds>();
const HEX32 = /^[0-9a-f]{32}$/;
const TRACEPARENT = /^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$/;
const NO_TRACE = "0".repeat(32);
let agent: string | undefined;

/** The `agent` field of every line; `main.ts` sets it to the card name at start. */
export function setLogAgent(name: string | undefined): void {
  agent = name;
}

/** Runs `fn` with `ids` on every line it logs, awaits included. */
export function withRequest<T>(ids: RequestIds, fn: () => T): T {
  return current.run(ids, fn);
}

/** The 32-hex trace id of a ctx: `traceparent`'s, else `trace_id` by the chassis's
 * `trace_id_hex` rule (32 lowercase hex as is, else the first 32 hex of its SHA-256). */
export function traceIdOf(ctx: Record<string, unknown>): string | undefined {
  const match = typeof ctx["traceparent"] === "string" ? TRACEPARENT.exec(ctx["traceparent"].trim().toLowerCase()) : null;
  if (match?.[1] && match[1] !== NO_TRACE) return match[1];
  const traceId = ctx["trace_id"];
  if (typeof traceId !== "string" || !traceId) return undefined;
  return HEX32.test(traceId) ? traceId : createHash("sha256").update(traceId).digest("hex").slice(0, 32);
}

/** The ids for a request's ctx: `request_id` and `trace_id` when the ctx has them. */
export function idsOf(ctx: Record<string, unknown>): RequestIds {
  const ids: RequestIds = {};
  if (typeof ctx["request_id"] === "string" && ctx["request_id"]) ids.request_id = ctx["request_id"];
  const traceId = traceIdOf(ctx);
  if (traceId) ids.trace_id = traceId;
  return ids;
}

/** One line, without the newline. */
export function formatLine(level: Level, logger: string, message: string, fields: Record<string, unknown> = {}): string {
  const line: Record<string, unknown> = { time: new Date().toISOString(), level, logger, message };
  if (agent) line["agent"] = agent;
  Object.assign(line, current.getStore() ?? {});
  for (const [key, value] of Object.entries(fields)) if (!(key in line)) line[key] = value;
  return JSON.stringify(line);
}

/** Writes one line: `info` and `debug` to stdout, `warning` and `error` to stderr. */
export function log(level: Level, logger: string, message: string, fields?: Record<string, unknown>): void {
  const line = formatLine(level, logger, message, fields);
  if (level === "error") console.error(line);
  else if (level === "warning") console.warn(line);
  else console.log(line);
}
