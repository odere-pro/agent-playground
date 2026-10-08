// The wire form, in memory: a JSON-RPC `SendStreamingMessage` body shaped like the one the
// chassis's Python a2a-sdk client sends, through the SDK's own JsonRpcTransportHandler (the code
// behind the Express route). No socket. It checks the JSON the chassis connector reads.

import assert from "node:assert/strict";
import { test } from "node:test";

import { JsonRpcTransportHandler, ServerCallContext } from "@a2a-js/sdk/server";

import { buildAgentCard, buildHandler } from "../src/a2a_server.js";
import { handle, type Handle, type Json } from "../src/handle.js";
import { CTX, sseBody, stubFetch } from "./stub.js";

const TRACEPARENT = `00-${CTX.trace_id}-00f067aa0ba902b7-01`;
// The HTTP header carries another value on purpose: the server must not read it.
const HEADER_TRACEPARENT = `00-${CTX.trace_id}-1111111111111111-01`;
const INPUT = { text: "Utilize simple words.", data: {} };

/** The raw result frames, as the JSON the chassis connector reads. */
async function frames(target: Handle, metadata: Json): Promise<Json[]> {
  const card = buildAgentCard({ name: "echo-typescript", version: "0.1.0", url: "http://127.0.0.1:9000" });
  const rpc = new JsonRpcTransportHandler(buildHandler(target, card));
  const body = {
    jsonrpc: "2.0",
    id: 1,
    method: "SendStreamingMessage",
    params: {
      message: {
        role: "ROLE_USER",
        messageId: "0b1c",
        contextId: CTX.trace_id,
        parts: [{ text: "Utilize simple words." }],
      },
      metadata,
    },
  };
  const context = new ServerCallContext({
    requestedVersion: "1.0",
    state: new Map([["headers", { traceparent: HEADER_TRACEPARENT }]]),
  });
  const out = await rpc.handle(body, context);
  assert.ok(Symbol.asyncIterator in out, "a streaming call returns a stream");
  const results: Json[] = [];
  for await (const frame of out as AsyncIterable<{ result?: Json }>) results.push(JSON.parse(JSON.stringify(frame.result)) as Json);
  return results;
}

function send(version: string, fetchImpl: typeof fetch): Promise<Json[]> {
  return frames((input, ctx, deps) => handle(input, ctx, { ...deps, fetch: fetchImpl }), {
    "chassis.ctx": JSON.stringify({ ...CTX, traceparent: TRACEPARENT }),
    "chassis.input": JSON.stringify(INPUT),
    "chassis.schema_version": version,
  });
}

/** The `chassis.event` string of one frame, as written. */
function rawEventOf(result: Json): unknown {
  const status = result["statusUpdate"] as Json | undefined;
  const artifact = result["artifactUpdate"] as Json | undefined;
  const meta = (status?.["metadata"] ?? (artifact?.["artifact"] as Json | undefined)?.["metadata"]) as Json | undefined;
  return meta?.["chassis.event"];
}

function eventOf(result: Json): Json | undefined {
  const raw = rawEventOf(result);
  if (raw === undefined) return undefined;
  assert.equal(typeof raw, "string", "chassis.event is a JSON string (v1)");
  return JSON.parse(raw as string) as Json;
}

test("wire: task, then one frame per chassis event, JSON as the Python connector reads it", async () => {
  const stub = stubFetch(200, sseBody(["Plain ", "words."]));
  const results = await send("0", stub.fetch);
  const task = results[0]?.["task"] as Json;
  assert.equal((task["status"] as Json)["state"], "TASK_STATE_SUBMITTED");
  const events = results.slice(1).map(eventOf);
  assert.deepEqual(events, [
    { schema_version: "0", type: "start", request_id: CTX.request_id },
    { schema_version: "0", type: "delta", text: "Plain " },
    { schema_version: "0", type: "delta", text: "words." },
    { schema_version: "0", type: "metrics", input_tokens: 12, output_tokens: 3, model_route: "local-small", attempt: 1 },
    { schema_version: "0", type: "end", status: "ok" },
  ]);
  const deltas = results.flatMap((r) => (r["artifactUpdate"] ? [r["artifactUpdate"] as Json] : []));
  assert.deepEqual(deltas.map((d) => [(d["artifact"] as Json)["artifactId"], d["append"] ?? false]), [["output", false], ["output", true]]);
  assert.deepEqual(((deltas[0]?.["artifact"] as Json)["parts"] as Json[])[0], { text: "Plain " });
  const last = results.at(-1)?.["statusUpdate"] as Json;
  assert.equal((last["status"] as Json)["state"], "TASK_STATE_COMPLETED");
  assert.equal(stub.calls[0]?.headers.get("traceparent"), TRACEPARENT, "ctx.traceparent, not the HTTP header");
});

test("wire: integers survive both ways as JSON strings", async () => {
  let got: [Json, Json] | undefined;
  const target: Handle = async function* (input, ctx) {
    got = [input, ctx];
    yield { schema_version: "0", type: "start", request_id: "r" };
    yield { schema_version: "0", type: "tool_call", call_id: "c1", name: "count", arguments: { n: 3 }, result: null };
    yield { schema_version: "0", type: "end", status: "ok", output: { count: 3 } };
  };
  const results = await frames(target, {
    "chassis.ctx": JSON.stringify({ ...CTX, budget: { max_tokens: 20, timeout_ms: 5000 } }),
    "chassis.input": JSON.stringify({ text: "x", data: { k: 7 } }),
    "chassis.schema_version": "0",
  });
  const k = (got?.[0]["data"] as Json)["k"];
  const maxTokens = (got?.[1]["budget"] as Json)["max_tokens"];
  assert.ok(Number.isInteger(k) && k === 7, `input.data.k is ${String(k)}`);
  assert.ok(Number.isInteger(maxTokens) && maxTokens === 20, `ctx.budget.max_tokens is ${String(maxTokens)}`);
  const raws = results.slice(1).map(rawEventOf);
  const toolCall = raws[1] as string;
  const end = raws[2] as string;
  assert.ok(toolCall.includes('"n":3') && !toolCall.includes("3.0"), toolCall);
  assert.ok(end.includes('"count":3') && !end.includes("3.0"), end);
  assert.deepEqual((JSON.parse(toolCall) as Json)["arguments"], { n: 3 });
  assert.deepEqual((JSON.parse(end) as Json)["output"], { count: 3 });
});

test("wire: the v0 object form of chassis.ctx is still read, input from the parts", async () => {
  const stub = stubFetch(200, sseBody(["ok"]));
  const results = await frames((input, ctx, deps) => handle(input, ctx, { ...deps, fetch: stub.fetch }), {
    "chassis.ctx": CTX,
    "chassis.schema_version": "0",
  });
  assert.equal(((results.at(-1)?.["statusUpdate"] as Json)["status"] as Json)["state"], "TASK_STATE_COMPLETED");
  assert.deepEqual((stub.calls[0]?.body["messages"] as Json[])[1], { role: "user", content: "Utilize simple words." });
  assert.equal(stub.calls[0]?.body["model"], CTX.model_route);
  assert.equal(stub.calls[0]?.headers.has("traceparent"), false, "no ctx.traceparent, no header");
});

test("wire: a chassis.ctx string that does not parse fails the task with a2a.bad_request", async () => {
  const stub = stubFetch(200, sseBody(["x"]));
  const results = await frames((input, ctx, deps) => handle(input, ctx, { ...deps, fetch: stub.fetch }), {
    "chassis.ctx": "{not json",
    "chassis.schema_version": "0",
  });
  assert.equal(stub.calls.length, 0);
  const last = results.at(-1)?.["statusUpdate"] as Json;
  assert.equal((last["status"] as Json)["state"], "TASK_STATE_FAILED");
  assert.equal(eventOf(results.at(-1) ?? {})?.["code"], "a2a.bad_request");
});

test("wire: an unsupported schema version fails the task with a2a.unsupported_schema_version", async () => {
  const stub = stubFetch(200, sseBody(["x"]));
  const results = await send("9", stub.fetch);
  assert.equal(stub.calls.length, 0);
  const last = results.at(-1)?.["statusUpdate"] as Json;
  assert.equal((last["status"] as Json)["state"], "TASK_STATE_FAILED");
  assert.equal(eventOf(results.at(-1) ?? {})?.["code"], "a2a.unsupported_schema_version");
});
