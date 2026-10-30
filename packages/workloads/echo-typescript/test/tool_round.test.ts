// One round of tool calls, the step `handle` runs after a model call that asked for tools.

import assert from "node:assert/strict";
import { test } from "node:test";

import { runToolRound, type ChassisEvent, type Json } from "../src/handle.js";
import { ToolCallError, callTool, type McpOptions, type ToolCall } from "../src/tools.js";
import { benchmarkTools, mcpStub } from "./stub.js";

const options = (send: typeof fetch): McpOptions => ({ fetch: send, headers: {}, timeoutMs: 5000, url: "http://t/mcp" });

async function drain(round: AsyncGenerator<ChassisEvent, Json[] | undefined>): Promise<[ChassisEvent[], Json[] | undefined]> {
  const events: ChassisEvent[] = [];
  for (;;) {
    const next = await round.next();
    if (next.done) return [events, next.value];
    events.push(next.value);
  }
}

test("two tools asked in one round yield two tool_call events in index order", async () => {
  const calls = new Map<number, ToolCall>([
    [1, { callId: "c2", name: "acronym_expand", arguments: '{"acronym":"RAG"}' }],
    [0, { callId: "c1", name: "glossary_lookup", arguments: '{"term":"SLM"}' }],
  ]);
  const [events] = await drain(runToolRound(calls, options(mcpStub(benchmarkTools).fetch)));
  assert.deepEqual(events.map((e) => [e.type, e["call_id"]]), [["tool_call", "c1"], ["tool_call", "c2"]]);
});

test("a finished round returns the assistant message and one tool message per call, in index order", async () => {
  const calls = new Map<number, ToolCall>([
    [1, { callId: "c2", name: "acronym_expand", arguments: '{"acronym":"RAG"}' }],
    [0, { callId: "c1", name: "glossary_lookup", arguments: '{"term":"SLM"}' }],
  ]);
  const [, messages] = await drain(runToolRound(calls, options(mcpStub(benchmarkTools).fetch)));
  assert.deepEqual(messages?.map((m) => [m["role"], m["tool_call_id"]]), [["assistant", undefined], ["tool", "c1"], ["tool", "c2"]]);
});

test("a round whose tool fails ends with tool_error and returns no messages", async () => {
  const calls = new Map<number, ToolCall>([[0, { callId: "c1", name: "glossary_lookup", arguments: "{}" }]]);
  const [events, messages] = await drain(runToolRound(calls, options(mcpStub(benchmarkTools, { transportError: true }).fetch)));
  assert.deepEqual([events.map((e) => [e.type, e["code"]]), messages], [[["error", "tool_error"]], undefined]);
});

test("a 5000-character transport error gives a tool_error message of at most 200 characters plus the tool name", async () => {
  const long = (async () => {
    throw new TypeError("x".repeat(5000));
  }) as typeof fetch;
  const failure = await callTool("glossary_lookup", {}, options(long)).then(() => undefined, (exc: unknown) => exc);
  assert.ok(failure instanceof ToolCallError);
  assert.ok(failure.message.startsWith("glossary_lookup: "), failure.message.slice(0, 40));
  assert.ok(failure.message.length <= "glossary_lookup: ".length + 200, String(failure.message.length));
});
