// The remote lane: with CHASSIS_API_TOKEN set, the model call carries `Authorization: Bearer
// <token>`; unset or empty, it carries none; the token is in no event and no console line.
// (`test/agent.test.ts` checks the same header on the MCP requests.)

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { handle, type ChassisEvent } from "../src/handle.js";
import { CTX, sseBody, stubFetch } from "./stub.js";

const TOKEN = "tok-remote-0123456789";
const TRACEPARENT = `00-${CTX.trace_id}-00f067aa0ba902b7-01`;
const saved = process.env["CHASSIS_API_TOKEN"];

afterEach(() => {
  if (saved === undefined) delete process.env["CHASSIS_API_TOKEN"];
  else process.env["CHASSIS_API_TOKEN"] = saved;
});

async function collect(gen: AsyncIterable<ChassisEvent>): Promise<ChassisEvent[]> {
  const out: ChassisEvent[] = [];
  for await (const event of gen) out.push(event);
  return out;
}

test("token set: the model call carries the bearer next to the traceparent", async () => {
  process.env["CHASSIS_API_TOKEN"] = TOKEN;
  const stub = stubFetch(200, sseBody(["ok"]));
  await collect(handle({ text: "x" }, { ...CTX, traceparent: TRACEPARENT }, { fetch: stub.fetch }));
  assert.equal(stub.calls.length, 1);
  assert.equal(stub.calls[0]?.headers.get("authorization"), `Bearer ${TOKEN}`);
  assert.equal(stub.calls[0]?.headers.get("traceparent"), TRACEPARENT);
});

test("token unset or empty: no Authorization header", async () => {
  const stub = stubFetch(200, sseBody(["ok"]));
  delete process.env["CHASSIS_API_TOKEN"];
  await collect(handle({ text: "x" }, CTX, { fetch: stub.fetch }));
  process.env["CHASSIS_API_TOKEN"] = "";
  await collect(handle({ text: "x" }, CTX, { fetch: stub.fetch }));
  assert.equal(stub.calls.length, 2);
  for (const call of stub.calls) assert.equal(call.headers.has("authorization"), false);
});

test("the token is in no event, on success or on error, and in no console line", async () => {
  process.env["CHASSIS_API_TOKEN"] = TOKEN;
  const lines: string[] = [];
  const spies = (["log", "warn", "error", "info", "debug"] as const).map((name) => {
    const original = console[name];
    console[name] = (...args: unknown[]) => void lines.push(args.map(String).join(" "));
    return () => void (console[name] = original);
  });
  try {
    const ok = await collect(handle({ text: "x" }, CTX, { fetch: stubFetch(200, sseBody(["ok"])).fetch }));
    const bad = await collect(handle({ text: "x" }, CTX, { fetch: stubFetch(401, "denied").fetch }));
    const down = (async () => {
      throw new Error("connect refused");
    }) as typeof fetch;
    const dead = await collect(handle({ text: "x" }, CTX, { fetch: down }));
    assert.equal(bad.at(-1)?.["type"], "error");
    assert.equal(dead.at(-1)?.["type"], "error");
    assert.equal(JSON.stringify([ok, bad, dead]).includes(TOKEN), false);
  } finally {
    spies.forEach((restore) => restore());
  }
  assert.equal(lines.join("\n").includes(TOKEN), false);
});
