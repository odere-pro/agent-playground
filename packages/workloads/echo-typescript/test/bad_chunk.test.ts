import assert from "node:assert/strict";
import { test } from "node:test";

import { handle, type ChassisEvent } from "../src/handle.js";
import { CTX, noTools, stubFetch } from "./stub.js";

/** R2-27: a streamed chunk of the wrong shape is a `bad_response`, never a retryable
 * `connect_error` from a `TypeError` caught by the outer `catch`. */

async function collect(gen: AsyncIterable<ChassisEvent>): Promise<ChassisEvent[]> {
  const out: ChassisEvent[] = [];
  for await (const event of gen) out.push(event);
  return out;
}

function body(payload: string): string {
  return `data: ${payload}\n\ndata: [DONE]\n\n`;
}

const BAD_CHUNKS: Record<string, string> = {
  "data: null": "null",
  "a number chunk": "5",
  "an array chunk": "[]",
  "choices not an array": JSON.stringify({ choices: 5 }),
  "a null choice": JSON.stringify({ choices: [null] }),
  "a string choice": JSON.stringify({ choices: ["x"] }),
  "a null tool piece": JSON.stringify({ choices: [{ delta: { tool_calls: [null] } }] }),
  "a number tool piece": JSON.stringify({ choices: [{ delta: { tool_calls: [7] } }] }),
};

for (const [name, payload] of Object.entries(BAD_CHUNKS)) {
  test(`${name} is a bad_response, not retryable`, async () => {
    const stub = stubFetch(200, body(payload));
    const events = await collect(handle({ text: "x" }, CTX, { fetch: stub.fetch, toolFetch: noTools }));
    assert.equal(events[0]?.type, "start");
    const last = events.at(-1);
    assert.equal(last?.type, "error", JSON.stringify(events));
    assert.equal(last?.["code"], "bad_response");
    assert.equal(last?.["retryable"], false);
    assert.equal(events.filter((e) => e.type === "error").length, 1);
  });
}

test("choices: null and an empty choices list are still read as no choices", async () => {
  const chunks = [{ choices: null }, { choices: [] }, { choices: [{ delta: { content: "ok" } }] }];
  const payload = chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n";
  const stub = stubFetch(200, payload);
  const events = await collect(handle({ text: "x" }, CTX, { fetch: stub.fetch, toolFetch: noTools }));
  assert.deepEqual(
    events.filter((e) => e.type === "delta").map((e) => e["text"]),
    ["ok"],
  );
  assert.equal(events.at(-1)?.type, "end");
});
