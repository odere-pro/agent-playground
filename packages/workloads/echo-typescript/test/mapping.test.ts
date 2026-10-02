import assert from "node:assert/strict";
import { test } from "node:test";

import { Role, TaskState, type Part, type SendMessageRequest } from "@a2a-js/sdk";
import {
  DefaultExecutionEventBus,
  RequestContext,
  ServerCallContext,
  type AgentExecutionEvent,
} from "@a2a-js/sdk/server";

import {
  CTX_KEY,
  EVENT_KEY,
  HandleExecutor,
  INPUT_KEY,
  SCHEMA_VERSION_KEY,
  buildAgentCard,
  eventToUpdate,
  requestToInput,
} from "../src/a2a_server.js";
import type { ChassisEvent, Deps, Json } from "../src/handle.js";
import { validateEvent } from "../src/schema.js";
import { CTX, sseBody, stubFetch } from "./stub.js";

const TRACEPARENT = `00-${CTX.trace_id}-00f067aa0ba902b7-01`;

function part(content: Part["content"], mediaType = ""): Part {
  return { content, metadata: undefined, filename: "", mediaType };
}

function request(meta: Json, parts?: Part[]): SendMessageRequest {
  return {
    tenant: "",
    configuration: undefined,
    metadata: meta,
    message: {
      messageId: "m-1",
      contextId: CTX.trace_id,
      taskId: "",
      role: Role.ROLE_USER,
      parts: parts ?? [
        part({ $case: "text", value: "Utilize simple words." }),
        part({ $case: "data", value: { level: "B1" } }, "application/json"),
      ],
      metadata: undefined,
      extensions: [],
      referenceTaskIds: [],
    },
  };
}

const META = { [CTX_KEY]: CTX, [SCHEMA_VERSION_KEY]: "0" };

const META_V1 = {
  [CTX_KEY]: JSON.stringify(CTX),
  [INPUT_KEY]: JSON.stringify({ text: "Utilize simple words.", data: { level: "B1" } }),
  [SCHEMA_VERSION_KEY]: "0",
};

/** Runs the executor on an in-memory bus: no socket, no server. */
async function run(
  handle: (input: Json, ctx: Json, deps?: Deps) => AsyncIterable<unknown>,
  req: SendMessageRequest = request(META),
  headers: Record<string, string> = {},
): Promise<AgentExecutionEvent[]> {
  const bus = new DefaultExecutionEventBus();
  const seen: AgentExecutionEvent[] = [];
  bus.on("event", (e) => seen.push(e));
  const call = new ServerCallContext({ state: new Map([["headers", headers]]) });
  const executor = new HandleExecutor(handle as never);
  await executor.execute(new RequestContext(req, "task-1", CTX.trace_id, call), bus);
  return seen;
}

function chassisEvents(seen: AgentExecutionEvent[]): Json[] {
  const out: Json[] = [];
  for (const e of seen) {
    const raw =
      e.kind === "statusUpdate" ? e.data.metadata?.[EVENT_KEY]
      : e.kind === "artifactUpdate" ? e.data.artifact?.metadata?.[EVENT_KEY]
      : undefined;
    if (raw === undefined) continue;
    assert.equal(typeof raw, "string", "chassis.event is a JSON string (v1)");
    out.push(JSON.parse(raw as string) as Json);
  }
  return out;
}

const ev = (type: string, fields: Json = {}): ChassisEvent => ({ schema_version: "0", type, ...fields });

// --- Request in ---

test("request in: text part, data part, ctx and schema version from the metadata", () => {
  const [input, ctx] = requestToInput(request(META));
  assert.deepEqual(input, { text: "Utilize simple words.", data: { level: "B1" } });
  assert.deepEqual(ctx, CTX);
});

test("request in: no parts gives empty input", () => {
  const [input] = requestToInput(request(META, []));
  assert.deepEqual(input, { text: null, data: {} });
});

test("request in (v1): ctx and input from JSON strings; the parts are not read", () => {
  const meta = { ...META_V1, [INPUT_KEY]: JSON.stringify({ text: "from metadata", data: { k: 7 } }) };
  const [input, ctx] = requestToInput(request(meta));
  assert.deepEqual(input, { text: "from metadata", data: { k: 7 } });
  assert.deepEqual(ctx, CTX);
});

test("request in (v1): no chassis.input falls back to the parts", () => {
  const [input, ctx] = requestToInput(request({ [CTX_KEY]: JSON.stringify(CTX) }));
  assert.deepEqual(input, { text: "Utilize simple words.", data: { level: "B1" } });
  assert.deepEqual(ctx, CTX);
});

test("request in: a string that does not parse to an object throws", () => {
  for (const bad of ["{not json", "[1]", "3", "null"]) {
    assert.throws(() => requestToInput(request({ [CTX_KEY]: bad })), /chassis\.ctx/, bad);
    assert.throws(() => requestToInput(request({ ...META_V1, [INPUT_KEY]: bad })), /chassis\.input/, bad);
  }
});

// --- Events out ---

test("events out: delta is an artifact update on `output`, the event in artifact metadata", () => {
  const first = eventToUpdate(ev("delta", { text: "Plain " }), "t", "c", false);
  assert.equal(first.kind, "artifactUpdate");
  if (first.kind !== "artifactUpdate") return;
  assert.equal(first.data.artifact?.artifactId, "output");
  assert.equal(first.data.append, false);
  assert.equal(first.data.lastChunk, false);
  assert.deepEqual(first.data.artifact?.parts[0]?.content, { $case: "text", value: "Plain " });
  assert.equal(first.data.artifact?.metadata?.[EVENT_KEY], JSON.stringify(ev("delta", { text: "Plain " })));
  const next = eventToUpdate(ev("delta", { text: "x" }), "t", "c", true);
  assert.equal(next.kind === "artifactUpdate" && next.data.append, true);
});

test("events out: states and native parts per the contract table", () => {
  const cases: [ChassisEvent, TaskState, Part["content"] | null][] = [
    [ev("start", { request_id: "r" }), TaskState.TASK_STATE_WORKING, null],
    [ev("metrics", { input_tokens: 12 }), TaskState.TASK_STATE_WORKING, null],
    [
      ev("tool_call", { call_id: "c1", name: "glossary_lookup", arguments: { term: "SLM" }, result: null }),
      TaskState.TASK_STATE_WORKING,
      { $case: "data", value: { call_id: "c1", name: "glossary_lookup", arguments: { term: "SLM" }, result: null } },
    ],
    [ev("end", { status: "ok" }), TaskState.TASK_STATE_COMPLETED, null],
    [ev("end", { status: "retry", output: { a: 1 } }), TaskState.TASK_STATE_COMPLETED, { $case: "data", value: { a: 1 } }],
    [ev("error", { code: "x", message: "boom" }), TaskState.TASK_STATE_FAILED, { $case: "text", value: "boom" }],
  ];
  for (const [event, state, content] of cases) {
    const update = eventToUpdate(event, "t", "c", false);
    assert.equal(update.kind, "statusUpdate");
    if (update.kind !== "statusUpdate") continue;
    assert.equal(update.data.status?.state, state, String(event.type));
    assert.equal(update.data.metadata?.[EVENT_KEY], JSON.stringify(event));
    const message = update.data.status?.message;
    if (content === null) assert.equal(message, undefined, String(event.type));
    else {
      assert.equal(message?.role, Role.ROLE_AGENT);
      assert.deepEqual(message?.parts[0]?.content, content, String(event.type));
    }
  }
});

test("executor: submitted task first, then one A2A event per chassis event, in order", async () => {
  const stub = stubFetch(200, sseBody(["Plain ", "words."]));
  const { handle } = await import("../src/handle.js");
  const req = request({ ...META_V1, [CTX_KEY]: JSON.stringify({ ...CTX, traceparent: TRACEPARENT }) });
  const seen = await run((i, c, d) => handle(i, c, { ...d, fetch: stub.fetch }), req);
  assert.equal(seen[0]?.kind, "task");
  if (seen[0]?.kind === "task") {
    assert.equal(seen[0].data.status?.state, TaskState.TASK_STATE_SUBMITTED);
    assert.equal(seen[0].data.id, "task-1");
  }
  assert.deepEqual(
    seen.slice(1).map((e) => e.kind),
    ["statusUpdate", "artifactUpdate", "artifactUpdate", "statusUpdate", "statusUpdate"],
  );
  assert.deepEqual(
    chassisEvents(seen).map((e) => e["type"]),
    ["start", "delta", "delta", "metrics", "end"],
  );
  const appends = seen.flatMap((e) => (e.kind === "artifactUpdate" ? [e.data.append] : []));
  assert.deepEqual(appends, [false, true]);
  assert.equal(stub.calls[0]?.headers.get("traceparent"), TRACEPARENT);
  assert.equal(stub.calls[0]?.headers.has("authorization"), false);
});

test("executor: ctx passes through unchanged; the HTTP header is not read into deps", async () => {
  const stub = stubFetch(200, sseBody(["ok"]));
  const { handle } = await import("../src/handle.js");
  const seenDeps: Deps[] = [];
  const seenCtx: Json[] = [];
  const spy = (i: Json, c: Json, d?: Deps) => {
    seenDeps.push(d ?? {});
    seenCtx.push(c);
    return handle(i, c, { ...d, fetch: stub.fetch });
  };
  await run(spy, request(META_V1), { traceparent: TRACEPARENT });
  assert.deepEqual(seenCtx[0], CTX);
  assert.deepEqual(Object.keys(seenDeps[0] ?? {}), ["signal"]);
  assert.equal(stub.calls[0]?.headers.has("traceparent"), false);
});

test("executor: integers in chassis.input and chassis.ctx reach handle as integers", async () => {
  let got: [Json, Json] | undefined;
  const meta = {
    ...META_V1,
    [INPUT_KEY]: JSON.stringify({ text: "x", data: { k: 7 } }),
    [CTX_KEY]: JSON.stringify({ ...CTX, budget: { max_tokens: 20, timeout_ms: 5000 } }),
  };
  await run((i, c) => {
    got = [i, c];
    return yields(ev("start", { request_id: "r" }), ev("end"));
  }, request(meta));
  const k = ((got?.[0]["data"] as Json)["k"]);
  const maxTokens = ((got?.[1]["budget"] as Json)["max_tokens"]);
  assert.equal(k, 7);
  assert.equal(Number.isInteger(k), true);
  assert.equal(maxTokens, 20);
  assert.equal(Number.isInteger(maxTokens), true);
});

test("executor: a chassis.ctx or chassis.input that does not parse is a2a.bad_request and FAILED", async () => {
  for (const meta of [{ ...META_V1, [CTX_KEY]: "{not json" }, { ...META_V1, [INPUT_KEY]: "[1]" }]) {
    let called = false;
    const seen = await run(() => {
      called = true;
      return yields();
    }, request(meta));
    assert.equal(called, false);
    const last = seen.at(-1);
    assert.equal(last?.kind === "statusUpdate" && last.data.status?.state, TaskState.TASK_STATE_FAILED);
    assert.equal(chassisEvents(seen).at(-1)?.["code"], "a2a.bad_request");
  }
});

test("a yielded event with NaN or Infinity is workload.bad_event", async () => {
  for (const bad of [Number.NaN, Number.POSITIVE_INFINITY]) {
    const out = chassisEvents(
      await run(() => yields(ev("start", { request_id: "r" }), ev("end", { output: { x: bad } }))),
    );
    assert.deepEqual(out.map((e) => e["type"]), ["start", "error"]);
    assert.equal(out[1]?.["code"], "workload.bad_event");
  }
});

async function* yields(...events: unknown[]): AsyncGenerator<unknown> {
  for (const e of events) yield e;
}

test("order: an event before start is workload.bad_order", async () => {
  const out = chassisEvents(await run(() => yields(ev("delta", { text: "x" }))));
  assert.equal(out.at(-1)?.["code"], "workload.bad_order");
});

test("order: a second start is workload.bad_order", async () => {
  const out = chassisEvents(await run(() => yields(ev("start", { request_id: "r" }), ev("start", { request_id: "r" }))));
  assert.deepEqual(out.map((e) => e["type"]), ["start", "error"]);
  assert.equal(out[1]?.["code"], "workload.bad_order");
});

test("order: returning without end is workload.no_end", async () => {
  const out = chassisEvents(await run(() => yields(ev("start", { request_id: "r" }))));
  assert.equal(out.at(-1)?.["code"], "workload.no_end");
});

test("order: nothing after end is published", async () => {
  const out = chassisEvents(
    await run(() => yields(ev("start", { request_id: "r" }), ev("end"), ev("delta", { text: "late" }))),
  );
  assert.deepEqual(out.map((e) => e["type"]), ["start", "end"]);
});

test("an exception in handle is workload.exception and FAILED", async () => {
  const seen = await run(async function* () {
    yield ev("start", { request_id: "r" });
    throw new Error("kaboom");
  });
  const last = seen.at(-1);
  assert.equal(last?.kind === "statusUpdate" && last.data.status?.state, TaskState.TASK_STATE_FAILED);
  assert.match(String(chassisEvents(seen).at(-1)?.["message"]), /Error: kaboom/);
  assert.equal(chassisEvents(seen).at(-1)?.["code"], "workload.exception");
});

test("a yielded event with an unknown field is workload.bad_event", async () => {
  const out = chassisEvents(await run(() => yields(ev("start", { request_id: "r", extra: 1 }))));
  assert.equal(out.at(-1)?.["code"], "workload.bad_event");
});

test("an unsupported request schema version is refused before handle runs", async () => {
  let called = false;
  const seen = await run(
    () => {
      called = true;
      return yields();
    },
    request({ [CTX_KEY]: CTX, [SCHEMA_VERSION_KEY]: "9" }),
  );
  assert.equal(called, false);
  assert.equal(seen[0]?.kind, "task");
  assert.equal(chassisEvents(seen).at(-1)?.["code"], "a2a.unsupported_schema_version");
});

test("cancel closes the generator and publishes canceled", async () => {
  const bus = new DefaultExecutionEventBus();
  const seen: AgentExecutionEvent[] = [];
  bus.on("event", (e) => seen.push(e));
  let closed = false;
  let release: () => void = () => undefined;
  const gate = new Promise<void>((r) => (release = r));
  const executor = new HandleExecutor((async function* () {
    try {
      yield ev("start", { request_id: "r" });
      await gate;
      yield ev("delta", { text: "never" });
      yield ev("end");
    } finally {
      closed = true;
    }
  }) as never);
  const call = new ServerCallContext({ state: new Map() });
  const running = executor.execute(new RequestContext(request(META), "task-1", CTX.trace_id, call), bus);
  await new Promise((r) => setTimeout(r, 5));
  await executor.cancelTask("task-1", bus);
  release();
  await running;
  const states = seen.flatMap((e) => (e.kind === "statusUpdate" ? [e.data.status?.state] : []));
  assert.equal(states.at(-1), TaskState.TASK_STATE_CANCELED);
  assert.deepEqual(chassisEvents(seen).map((e) => e["type"]), ["start"]);
  assert.equal(closed, true);
});

// --- Schema check ---

test("schema: every event kind the contract names validates", () => {
  for (const e of [
    ev("start", { request_id: "r" }),
    ev("delta", { text: "t" }),
    ev("tool_call", { call_id: "c", name: "n", arguments: {}, result: null }),
    ev("metrics", { input_tokens: 1, output_tokens: 2, cost_usd: 0.1, latency_ms: null, model_route: "m", attempt: 1 }),
    ev("end", { status: "fallback", output: null }),
    ev("error", { code: "c", message: "m", retryable: true }),
  ])
    assert.deepEqual(validateEvent(e), e);
});

test("schema: refuses an unknown field, a wrong schema_version, a missing field, a wrong type", () => {
  assert.throws(() => validateEvent(ev("delta", { text: "t", extra: 1 })), /extra/);
  assert.throws(() => validateEvent({ ...ev("delta", { text: "t" }), schema_version: "1" }), /schema_version/);
  assert.throws(() => validateEvent(ev("start")), /request_id/);
  assert.throws(() => validateEvent(ev("metrics", { input_tokens: 1.5 })), /input_tokens/);
  assert.throws(() => validateEvent(ev("end", { status: "maybe" })), /status/);
  assert.throws(() => validateEvent(ev("nope")), /type/);
  assert.throws(() => validateEvent("not an object"), /object/);
});

// --- Agent card ---

test("agent card: one skill handle, streaming, text and JSON modes, JSON-RPC 1.0", () => {
  const card = buildAgentCard({ name: "echo-typescript", version: "0.1.0", url: "http://127.0.0.1:9000" });
  assert.equal(card.description, "chassis workload echo-typescript");
  assert.equal(card.capabilities?.streaming, true);
  assert.deepEqual(card.defaultInputModes, ["text/plain", "application/json"]);
  assert.deepEqual(card.defaultOutputModes, ["text/plain", "application/json"]);
  assert.deepEqual(card.skills.map((s) => [s.id, s.name, s.tags]), [["handle", "handle", ["chassis"]]]);
  assert.deepEqual(card.supportedInterfaces[0], {
    url: "http://127.0.0.1:9000/",
    protocolBinding: "JSONRPC",
    protocolVersion: "1.0",
    tenant: "",
  });
});
