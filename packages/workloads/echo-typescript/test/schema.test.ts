// The event check fills the schema's defaults as the Python template's `validate_event` does
// (packages/workload-a2a/src/workload_a2a/server.py), so one event puts the same chassis.event on
// the wire from either server. The expected objects are that function's output.

import assert from "node:assert/strict";
import { test } from "node:test";

import { DefaultExecutionEventBus, RequestContext, ServerCallContext, type AgentExecutionEvent } from "@a2a-js/sdk/server";
import { Role } from "@a2a-js/sdk";

import schema from "../schemas/events.v0.json" with { type: "json" };
import { EVENT_KEY, HandleExecutor, SUPPORTED_SCHEMA_VERSIONS } from "../src/a2a_server.js";
import type { Handle, Json } from "../src/handle.js";
import { SCHEMA_VERSION, validateEvent } from "../src/schema.js";
import { CTX } from "./stub.js";

const ev = (type: string, fields: Json = {}): Json => ({ schema_version: "0", type, ...fields });

async function published(events: Json[]): Promise<Json[]> {
  const target: Handle = async function* () {
    for (const e of events) yield e as never;
  };
  const bus = new DefaultExecutionEventBus();
  const seen: AgentExecutionEvent[] = [];
  bus.on("event", (e) => seen.push(e));
  const req = {
    tenant: "",
    configuration: undefined,
    metadata: { "chassis.ctx": JSON.stringify(CTX) },
    message: { messageId: "m", contextId: "c", taskId: "", role: Role.ROLE_USER, parts: [], metadata: undefined, extensions: [], referenceTaskIds: [] },
  };
  await new HandleExecutor(target).execute(new RequestContext(req, "t", "c", new ServerCallContext({})), bus);
  return seen.flatMap((e) => (e.kind === "statusUpdate" && e.data.metadata ? [JSON.parse(String(e.data.metadata[EVENT_KEY])) as Json] : []));
}

test("a metrics event with only the counts, route, and attempt is published with cost_usd and latency_ms null", async () => {
  const metrics = ev("metrics", { input_tokens: 12, output_tokens: 3, model_route: "local-small", attempt: 1 });
  const out = await published([ev("start", { request_id: "r" }), metrics, ev("end", { status: "ok" })]);
  assert.deepEqual(out[1], {
    attempt: 1, cost_usd: null, input_tokens: 12, latency_ms: null, model_route: "local-small", output_tokens: 3, schema_version: "0", type: "metrics",
  });
});

test("a tool_call without arguments gets an empty arguments object", () => {
  assert.deepEqual(validateEvent(ev("tool_call", { call_id: "c1", name: "n", result: null })), {
    result: null, schema_version: "0", type: "tool_call", arguments: {}, call_id: "c1", name: "n",
  });
});

test("an end without output gets output null", () => {
  assert.deepEqual(validateEvent(ev("end", { status: "ok" })), { output: null, schema_version: "0", status: "ok", type: "end" });
});

test("two filled events never share one default object", () => {
  const a = validateEvent(ev("tool_call", { call_id: "a", name: "n" }));
  const b = validateEvent(ev("tool_call", { call_id: "b", name: "n" }));
  assert.notEqual(a["arguments"], b["arguments"]);
});

test("a field the event sets wins over the default", () => {
  assert.equal(validateEvent(ev("metrics", { cost_usd: 0.5 }))["cost_usd"], 0.5);
});

test("the supported versions are the schema_version consts in the vendored schema", () => {
  const consts = Object.values(schema.$defs).map((d) => d.properties.schema_version.const);
  assert.deepEqual(SUPPORTED_SCHEMA_VERSIONS, [...new Set(consts)].sort());
});

test("the current version is the newest supported one", () => {
  assert.equal(SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS.at(-1));
});

test("the event's own keys keep their order and the missing defaults come after them", () => {
  const out = validateEvent({ type: "metrics", input_tokens: 1, schema_version: "0", output_tokens: 2 });
  assert.deepEqual(Object.keys(out), ["type", "input_tokens", "schema_version", "output_tokens", "attempt", "cost_usd", "latency_ms", "model_route"]);
});

test("an event that ends with schema_version keeps it last, the missing defaults before it", () => {
  const out = validateEvent({ type: "end", status: "ok", schema_version: "0" });
  assert.deepEqual(Object.keys(out), ["type", "status", "output", "schema_version"]);
});
