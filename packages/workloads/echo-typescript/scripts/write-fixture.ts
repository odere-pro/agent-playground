// Regenerates packages/chassis/tests/fixtures/events_from_typescript.jsonl from this workload's
// `handle`, with a stubbed model (no socket). `handle` makes no tool call, so the fixture's one
// `tool_call` line is kept by hand (TOOL_CALL below) and placed after the first delta, as before.
// Every line is checked against the vendored events.v0.json before it is written.

import { writeFileSync } from "node:fs";

import { handle, type ChassisEvent } from "../src/handle.js";
import { validateEvent } from "../src/schema.js";
import { CTX, sseBody, stubFetch } from "../test/stub.js";

const TARGET = new URL("../../../chassis/tests/fixtures/events_from_typescript.jsonl", import.meta.url);
const TOOL_CALL: ChassisEvent = {
  type: "tool_call",
  call_id: "call_01",
  name: "glossary_lookup",
  arguments: { term: "SLM" },
  result: { definition: "small language model" },
  schema_version: "0",
};

const stub = stubFetch(200, sseBody(["Plain ", "words."], { prompt_tokens: 12, completion_tokens: 3 }));
const events: ChassisEvent[] = [];
for await (const event of handle({ text: "Utilize simple words." }, CTX, { fetch: stub.fetch })) events.push(event);
events.splice(events.findIndex((e) => e.type === "delta") + 1, 0, TOOL_CALL);

// The fixture's shape: `type` first, `schema_version` last.
const lines = events.map(({ type, schema_version, ...rest }) => JSON.stringify(validateEvent({ type, ...rest, schema_version })));
writeFileSync(TARGET, lines.join("\n") + "\n");
console.log(`wrote ${lines.length} events to ${TARGET.pathname}`);
