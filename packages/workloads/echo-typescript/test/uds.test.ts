// The model call over a Unix socket (CHASSIS_MODEL_UDS), against a real HTTP server on a Unix
// socket in a temp folder. No TCP. The PoC-2 gate runs the whole workload this way.

import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { handle, udsFetch, type ChassisEvent } from "../src/handle.js";
import { CTX, noTools, sseBody } from "./stub.js";

const TRACEPARENT = `00-${CTX.trace_id}-00f067aa0ba902b7-01`;

interface Seen {
  method: string | undefined;
  url: string | undefined;
  headers: IncomingMessage["headers"];
  body: Record<string, unknown>;
}

/** An HTTP server on a fresh Unix socket; `reply` answers each request. */
async function serve(reply: (res: ServerResponse) => void): Promise<{ path: string; seen: Seen[]; close: () => Promise<void> }> {
  const folder = mkdtempSync(join(tmpdir(), "ts-uds-"));
  const path = join(folder, "model.sock");
  const seen: Seen[] = [];
  const server = createServer((req, res) => {
    let raw = "";
    req.on("data", (chunk: Buffer) => (raw += chunk.toString()));
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, headers: req.headers, body: JSON.parse(raw) as Record<string, unknown> });
      reply(res);
    });
  });
  await new Promise<void>((resolve) => server.listen(path, resolve));
  const close = async (): Promise<void> => {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    rmSync(folder, { recursive: true, force: true });
  };
  return { path, seen, close };
}

async function collect(gen: AsyncIterable<ChassisEvent>): Promise<ChassisEvent[]> {
  const out: ChassisEvent[] = [];
  for await (const event of gen) out.push(event);
  return out;
}

test("CHASSIS_MODEL_UDS: the model call goes over the Unix socket, path and headers kept", async () => {
  const model = await serve((res) => {
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.end(sseBody(["Plain ", "words."], { prompt_tokens: 42, completion_tokens: 9 }));
  });
  process.env["CHASSIS_MODEL_UDS"] = model.path;
  try {
    const events = await collect(handle({ text: "simplify: x" }, { ...CTX, traceparent: TRACEPARENT }, { modelUrl: "http://127.0.0.1:8090/v1", toolFetch: noTools }));
    assert.deepEqual(events.map((e) => e.type), ["start", "delta", "delta", "metrics", "end"]);
    assert.equal(events.filter((e) => e.type === "delta").map((e) => e["text"]).join(""), "Plain words.");
    assert.deepEqual([events[3]?.["input_tokens"], events[3]?.["output_tokens"]], [42, 9]);
    const [call] = model.seen;
    assert.equal(model.seen.length, 1);
    assert.equal(call?.method, "POST");
    assert.equal(call?.url, "/v1/chat/completions");
    assert.equal(call?.headers["host"], "127.0.0.1:8090");
    assert.equal(call?.headers["traceparent"], TRACEPARENT);
    assert.equal(call?.headers["authorization"], undefined);
    assert.equal(call?.body["model"], CTX.model_route);
  } finally {
    delete process.env["CHASSIS_MODEL_UDS"];
    await model.close();
  }
});

test("CHASSIS_MODEL_UDS: an HTTP 500 over the socket is a retryable error", async () => {
  const model = await serve((res) => {
    res.writeHead(500, { "content-type": "text/plain" });
    res.end("scripted failure");
  });
  process.env["CHASSIS_MODEL_UDS"] = model.path;
  try {
    const events = await collect(handle({ text: "x" }, CTX, { toolFetch: noTools }));
    assert.deepEqual(events[1], { schema_version: "0", type: "error", code: "http_500", message: "scripted failure", retryable: true });
  } finally {
    delete process.env["CHASSIS_MODEL_UDS"];
    await model.close();
  }
});

test("CHASSIS_MODEL_UDS: the budget timeout fires mid-stream as a timeout error", async () => {
  const model = await serve((res) => {
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: "Plain " } }] })}\n\n`); // then stalls
  });
  process.env["CHASSIS_MODEL_UDS"] = model.path;
  try {
    const ctx = { ...CTX, budget: { max_tokens: 2000, timeout_ms: 300 } };
    const events = await collect(handle({ text: "x" }, ctx, { toolFetch: noTools }));
    assert.deepEqual(events.map((e) => e.type), ["start", "delta", "error"]);
    assert.equal(events[2]?.["code"], "timeout");
    assert.equal(events[2]?.["retryable"], true);
  } finally {
    delete process.env["CHASSIS_MODEL_UDS"];
    await model.close();
  }
});

test("udsFetch: no socket at the path is a connect_error, not a hang", async () => {
  const folder = mkdtempSync(join(tmpdir(), "ts-uds-"));
  try {
    const events = await collect(handle({ text: "x" }, CTX, { fetch: udsFetch(join(folder, "missing.sock")) }));
    assert.equal(events[1]?.["code"], "connect_error");
    assert.equal(events[1]?.["retryable"], true);
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
});

test("udsFetch: an already-aborted signal rejects with its reason, nothing sent", async () => {
  const model = await serve((res) => res.end());
  try {
    const signal = AbortSignal.abort(new Error("gone"));
    await assert.rejects(udsFetch(model.path)("http://127.0.0.1:8090/v1/x", { method: "POST", body: "{}", signal }), /gone/);
    assert.equal(model.seen.length, 0);
  } finally {
    await model.close();
  }
});
