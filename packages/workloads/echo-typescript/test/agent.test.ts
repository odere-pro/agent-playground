// The three benchmark tasks and the tool loop, with the model and the chassis's MCP tools stubbed
// (no TCP): smoke, simplifier, lookup; the round limit; a failed tool; an unreachable tool
// endpoint; `traceparent` and the bearer on every model and MCP request; events against the schema.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { handle, MAX_TOOL_ROUNDS, type ChassisEvent, type Deps } from "../src/handle.js";
import { validateEvent } from "../src/schema.js";
import { toolStats } from "../src/tools.js";
import {
  benchmarkTools,
  CTX,
  GLOSSARY,
  mcpStub,
  noTools,
  scriptedModel,
  sseBody,
  sseChunks,
  sseToolCall,
} from "./stub.js";

const TOKEN = "tok-agent-0123456789";
const TRACEPARENT = `00-${CTX.trace_id}-00f067aa0ba902b7-01`;
const saved = process.env["CHASSIS_API_TOKEN"];

afterEach(() => {
  if (saved === undefined) delete process.env["CHASSIS_API_TOKEN"];
  else process.env["CHASSIS_API_TOKEN"] = saved;
});

async function run(text: string, deps: Deps, ctx: Record<string, unknown> = CTX): Promise<ChassisEvent[]> {
  const out: ChassisEvent[] = [];
  for await (const event of handle({ text }, ctx, { modelUrl: "http://m/v1", ...deps })) {
    out.push(validateEvent(event));
  }
  return out;
}

const asks = (index: number, id: string, name: string, args: string): string =>
  sseChunks([sseToolCall(index, { id, name, arguments: args })]);
const types = (events: ChassisEvent[]): string[] => events.map((e) => e.type);

test("smoke: end ok comes after at least one delta", async () => {
  const model = scriptedModel([sseBody(["Hello", "."])]);
  const events = await run("simplify: Hello.", { fetch: model.fetch, toolFetch: mcpStub(benchmarkTools).fetch });
  const end = events.findIndex((e) => e.type === "end");
  assert.equal(events[end]?.["status"], "ok");
  assert.ok(events.findIndex((e) => e.type === "delta") >= 0 && events.findIndex((e) => e.type === "delta") < end);
});

test("simplifier: the reply keeps 2026, Acme, and 30", async () => {
  const reply = "Acme put out the SLM in 2026. It cut costs by 30 percent.";
  const model = scriptedModel([sseBody([reply])]);
  const text = "simplify: The SLM, released in 2026 by Acme, cut costs by 30 percent.";
  const events = await run(text, { fetch: model.fetch, toolFetch: mcpStub(benchmarkTools).fetch });
  const out = events.filter((e) => e.type === "delta").map((e) => e["text"]).join("");
  for (const fact of ["2026", "Acme", "30"]) assert.ok(out.includes(fact), fact);
  assert.deepEqual(types(events), ["start", "delta", "metrics", "end"]);
  assert.equal(model.calls[0]?.body["messages"] instanceof Array, true);
});

test("lookup: two tool calls in order, then the answer", async () => {
  const answer = "SLM means a small language model. RAG stands for retrieval-augmented generation.";
  // The first ask arrives split over three chunks, joined by index.
  const first = sseChunks([
    sseToolCall(0, { id: "call_1", name: "glossary_lookup", arguments: "" }),
    sseToolCall(0, { arguments: '{"term":' }),
    sseToolCall(0, { arguments: '"SLM"}' }),
  ]);
  const model = scriptedModel([first, asks(0, "call_2", "acronym_expand", '{"acronym":"RAG"}'), sseBody([answer])]);
  const mcp = mcpStub(benchmarkTools);
  const events = await run("lookup: Define SLM and expand RAG.", { fetch: model.fetch, toolFetch: mcp.fetch });
  assert.deepEqual(types(events), ["start", "tool_call", "tool_call", "delta", "metrics", "end"]);
  const calls = events.filter((e) => e.type === "tool_call");
  assert.deepEqual(calls[0], {
    schema_version: "0",
    type: "tool_call",
    call_id: "call_1",
    name: "glossary_lookup",
    arguments: { term: "SLM" },
    result: { term: "SLM", definition: GLOSSARY },
  });
  assert.deepEqual(calls[1]?.["result"], { acronym: "RAG", expansion: "retrieval-augmented generation" });
  const out = events.filter((e) => e.type === "delta").map((e) => e["text"]).join("");
  assert.ok(out.includes("a small language model") && out.includes("retrieval-augmented generation"));
  // Three model calls; the tools are offered; the tool messages are fed back; tokens are summed.
  assert.equal(model.calls.length, 3);
  const offered = model.calls[0]?.body["tools"] as { function: { name: string } }[];
  assert.deepEqual(offered.map((t) => t.function.name), ["glossary_lookup", "acronym_expand"]);
  const third = model.calls[2]?.body["messages"] as Record<string, unknown>[];
  assert.deepEqual(third.map((m) => m["role"]), ["system", "user", "assistant", "tool", "assistant", "tool"]);
  assert.deepEqual(third[2], {
    role: "assistant",
    content: null,
    tool_calls: [{ id: "call_1", type: "function", function: { name: "glossary_lookup", arguments: '{"term":"SLM"}' } }],
  });
  assert.equal(third[3]?.["tool_call_id"], "call_1");
  assert.deepEqual(JSON.parse(String(third[3]?.["content"])), { term: "SLM", definition: GLOSSARY });
  assert.deepEqual([events.at(-2)?.["input_tokens"], events.at(-2)?.["output_tokens"]], [36, 9]);
});

test("two tools asked in one answer run in index order", async () => {
  const both = sseChunks([
    sseToolCall(1, { id: "b", name: "acronym_expand", arguments: '{"acronym":"RAG"}' }),
    sseToolCall(0, { id: "a", name: "glossary_lookup", arguments: '{"term":"SLM"}' }),
  ]);
  const model = scriptedModel([both, sseBody(["done"])]);
  const events = await run("x", { fetch: model.fetch, toolFetch: mcpStub(benchmarkTools).fetch });
  assert.deepEqual(events.filter((e) => e.type === "tool_call").map((e) => e["call_id"]), ["a", "b"]);
});

test("a 4th ask is tool_loop_exceeded, not retryable", async () => {
  const model = scriptedModel([asks(0, "c", "glossary_lookup", '{"term":"SLM"}')]);
  const events = await run("x", { fetch: model.fetch, toolFetch: mcpStub(benchmarkTools).fetch });
  assert.equal(MAX_TOOL_ROUNDS, 3);
  assert.equal(events.filter((e) => e.type === "tool_call").length, 3);
  assert.equal(model.calls.length, 4);
  const last = events.at(-1);
  assert.equal(last?.["type"], "error");
  assert.equal(last?.["code"], "tool_loop_exceeded");
  assert.equal(last?.["retryable"], false);
  assert.equal(types(events).includes("end"), false);
});

test("a tool that fails (isError) is tool_error, not retryable", async () => {
  const model = scriptedModel([asks(0, "c", "glossary_lookup", '{"term":"SLM"}')]);
  const mcp = mcpStub({
    glossary_lookup: () => {
      throw new Error("glossary offline");
    },
  });
  const events = await run("x", { fetch: model.fetch, toolFetch: mcp.fetch });
  assert.deepEqual(types(events), ["start", "error"]);
  assert.equal(events[1]?.["code"], "tool_error");
  assert.equal(events[1]?.["retryable"], false);
  assert.match(String(events[1]?.["message"]), /glossary offline/);
});

test("a tools/call transport failure is tool_error", async () => {
  const model = scriptedModel([asks(0, "c", "glossary_lookup", '{"term":"SLM"}')]);
  const mcp = mcpStub(benchmarkTools, { transportError: true });
  const events = await run("x", { fetch: model.fetch, toolFetch: mcp.fetch });
  assert.equal(events.at(-1)?.["code"], "tool_error");
});

test("arguments that are not a JSON object are bad_response", async () => {
  for (const args of ['["SLM"]', "not json", "42"]) {
    const model = scriptedModel([asks(0, "c", "glossary_lookup", args)]);
    const events = await run("x", { fetch: model.fetch, toolFetch: mcpStub(benchmarkTools).fetch });
    assert.deepEqual(types(events), ["start", "error"], args);
    assert.equal(events[1]?.["code"], "bad_response", args);
    assert.equal(events[1]?.["retryable"], false, args);
  }
});

test("an unreachable tool endpoint: the run goes on with no tools, counted and warned", async () => {
  const warnings: string[] = [];
  const warn = console.warn;
  console.warn = (...args: unknown[]) => void warnings.push(args.map(String).join(" "));
  const before = toolStats.listFailures;
  try {
    const model = scriptedModel([sseBody(["fine"])]);
    const events = await run("x", { fetch: model.fetch, toolFetch: noTools });
    assert.deepEqual(types(events), ["start", "delta", "metrics", "end"]);
    assert.equal("tools" in (model.calls[0]?.body ?? {}), false);
  } finally {
    console.warn = warn;
  }
  assert.equal(toolStats.listFailures, before + 1);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0] ?? "", /unreachable/);
});

test("traceparent and the bearer go on every model call and every MCP request", async () => {
  process.env["CHASSIS_API_TOKEN"] = TOKEN;
  const model = scriptedModel([asks(0, "c", "glossary_lookup", '{"term":"SLM"}'), sseBody(["ok"])]);
  const mcp = mcpStub(benchmarkTools);
  await run("x", { fetch: model.fetch, toolFetch: mcp.fetch }, { ...CTX, traceparent: TRACEPARENT });
  assert.equal(model.calls.length, 2);
  assert.ok(mcp.requests.length >= 6, "list session and call session, each with initialize");
  for (const headers of [...model.calls.map((c) => c.headers), ...mcp.requests.map((r) => r.headers)]) {
    assert.equal(headers.get("traceparent"), TRACEPARENT);
    assert.equal(headers.get("authorization"), `Bearer ${TOKEN}`);
  }
});

test("no token and no traceparent: neither header on any call", async () => {
  delete process.env["CHASSIS_API_TOKEN"];
  const model = scriptedModel([asks(0, "c", "glossary_lookup", '{"term":"SLM"}'), sseBody(["ok"])]);
  const mcp = mcpStub(benchmarkTools);
  await run("x", { fetch: model.fetch, toolFetch: mcp.fetch });
  process.env["CHASSIS_API_TOKEN"] = "";
  await run("x", { fetch: model.fetch, toolFetch: mcp.fetch });
  for (const headers of [...model.calls.map((c) => c.headers), ...mcp.requests.map((r) => r.headers)]) {
    assert.equal(headers.has("traceparent"), false);
    assert.equal(headers.has("authorization"), false);
  }
});

test("the token is in no event, warning, or error", async () => {
  process.env["CHASSIS_API_TOKEN"] = TOKEN;
  const seen: string[] = [];
  const warn = console.warn;
  console.warn = (...args: unknown[]) => void seen.push(args.map(String).join(" "));
  try {
    const model = scriptedModel([asks(0, "c", "glossary_lookup", '{"term":"SLM"}')]);
    const events = await run("x", { fetch: model.fetch, toolFetch: mcpStub(benchmarkTools, { transportError: true }).fetch });
    const down = await run("x", { fetch: model.fetch, toolFetch: noTools });
    seen.push(JSON.stringify([events, down]));
  } finally {
    console.warn = warn;
  }
  assert.equal(seen.join("\n").includes(TOKEN), false);
});
