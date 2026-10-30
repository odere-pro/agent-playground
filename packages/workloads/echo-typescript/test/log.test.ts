// The workload's log lines (PoC-8, 014 H-7): one JSON object per line with the chassis's field
// names (`time`, `level`, `logger`, `message`, `agent`), and inside an A2A request the request's
// `request_id` and 32-hex `trace_id`, read from `chassis.ctx`.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";

import { DefaultExecutionEventBus, type RequestContext } from "@a2a-js/sdk/server";

import { CTX_KEY, HandleExecutor, INPUT_KEY } from "../src/a2a_server.js";
import { SCHEMA_VERSION, type ChassisEvent } from "../src/handle.js";
import { formatLine, log, setLogAgent, traceIdOf, withRequest } from "../src/log.js";
import { CTX } from "./stub.js";

const TRACE = "4bf92f3577b34da6a336f7ef8e2a8f4e";
const TRACEPARENT = `00-${TRACE}-00f067aa0ba902b7-01`;
const TIME = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/;

/** The JSON lines `fn` writes through `console.log`, `console.warn`, and `console.error`. */
async function captured(fn: () => Promise<void> | void): Promise<Record<string, unknown>[]> {
  const lines: string[] = [];
  const saved = { log: console.log, warn: console.warn, error: console.error };
  const push = (...args: unknown[]): void => void lines.push(args.map(String).join(" "));
  Object.assign(console, { log: push, warn: push, error: push });
  try {
    await fn();
  } finally {
    Object.assign(console, saved);
  }
  return lines.map((line) => JSON.parse(line) as Record<string, unknown>);
}

test("a line is one JSON object with the chassis's field names", () => {
  setLogAgent("echo-typescript");
  const line = formatLine("warning", "echo_typescript.tools", "two\nlines", { route: "r1" });
  assert.equal(line.includes("\n"), false);
  const parsed = JSON.parse(line) as Record<string, unknown>;
  assert.deepEqual(Object.keys(parsed).slice(0, 5), ["time", "level", "logger", "message", "agent"]);
  assert.match(String(parsed["time"]), TIME);
  assert.equal(parsed["level"], "warning");
  assert.equal(parsed["message"], "two\nlines");
  assert.equal(parsed["agent"], "echo-typescript");
  assert.equal(parsed["route"], "r1");
  assert.equal("request_id" in parsed, false);
  assert.equal("trace_id" in parsed, false);
});

test("inside withRequest a line carries the ids; outside it does not", async () => {
  const inside = await withRequest({ request_id: "req-9", trace_id: TRACE }, async () => {
    await Promise.resolve();
    return JSON.parse(formatLine("info", "x", "busy")) as Record<string, unknown>;
  });
  assert.equal(inside["request_id"], "req-9");
  assert.equal(inside["trace_id"], TRACE);
  const after = JSON.parse(formatLine("info", "x", "idle")) as Record<string, unknown>;
  assert.equal("trace_id" in after, false);
});

test("the trace id is ctx.traceparent's, else ctx.trace_id as the chassis hashes it", () => {
  assert.equal(traceIdOf({ traceparent: TRACEPARENT, trace_id: "x" }), TRACE);
  assert.equal(traceIdOf({ traceparent: "garbage", trace_id: TRACE }), TRACE);
  assert.equal(traceIdOf({ trace_id: "trace-1" }), createHash("sha256").update("trace-1").digest("hex").slice(0, 32));
  assert.equal(traceIdOf({}), undefined);
});

test("log writes warnings through console.warn as JSON", async () => {
  const lines = await captured(() => log("warning", "echo_typescript.tools", "unreachable"));
  assert.equal(lines.length, 1);
  assert.equal(lines[0]?.["level"], "warning");
});

function requestContext(ctx: Record<string, unknown>): RequestContext {
  const metadata = { [CTX_KEY]: JSON.stringify(ctx), [INPUT_KEY]: JSON.stringify({ text: "hi" }) };
  return {
    taskId: "task-1",
    contextId: "ctx-1",
    request: { message: undefined, metadata },
  } as unknown as RequestContext;
}

test("a line the workload logs inside a request carries its request_id and trace_id", async () => {
  const executor = new HandleExecutor(async function* (_input, ctx): AsyncGenerator<ChassisEvent> {
    log("info", "my_workload", "working on it");
    yield { schema_version: SCHEMA_VERSION, type: "start", request_id: ctx["request_id"] };
    yield { schema_version: SCHEMA_VERSION, type: "end", status: "ok" };
  });
  const ctx = { ...CTX, traceparent: TRACEPARENT };
  const lines = await captured(() => executor.execute(requestContext(ctx), new DefaultExecutionEventBus()));
  const business = lines.find((l) => l["logger"] === "my_workload");
  assert.equal(business?.["request_id"], CTX.request_id);
  assert.equal(business?.["trace_id"], TRACE);
  const done = lines.find((l) => l["message"] === "handle done");
  assert.equal(done?.["trace_id"], TRACE);
  assert.equal(done?.["request_id"], CTX.request_id);
  const outside = await captured(() => log("info", "my_workload", "after"));
  assert.equal("trace_id" in (outside[0] ?? {}), false);
});
