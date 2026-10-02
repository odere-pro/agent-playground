// The task store forgets a task a short wait after it reaches a final state, as the Python
// template's PruningRequestHandler does (pocs/poc-04-stateless-scalable/notes/
// 2026-10-01-hidden-state.md). No socket: the store directly, then one streaming call through
// the SDK's JsonRpcTransportHandler.

import assert from "node:assert/strict";
import { setTimeout as sleep } from "node:timers/promises";
import { test } from "node:test";

import { TaskState, type Task } from "@a2a-js/sdk";
import { JsonRpcTransportHandler, ServerCallContext } from "@a2a-js/sdk/server";

import { PRUNE_DELAY_MS, PruningTaskStore, buildAgentCard, buildHandler } from "../src/a2a_server.js";
import { CTX } from "./stub.js";
import { SCHEMA_VERSION, type Handle } from "../src/handle.js";

const WAIT_MS = 10;
const context = (): ServerCallContext => new ServerCallContext({ requestedVersion: "1.0" });
const task = (id: string, state: TaskState): Task => ({
  id,
  contextId: "c",
  status: { state, message: undefined, timestamp: new Date().toISOString() },
  artifacts: [],
  history: [],
  metadata: undefined,
});

test("prune: the default wait is the Python template's 3 s (suggested)", () => {
  assert.equal(PRUNE_DELAY_MS, 3000);
});

test("prune: a final task is kept for the wait, then forgotten; a working task stays", async () => {
  const store = new PruningTaskStore(WAIT_MS);
  for (const state of [TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_CANCELED, TaskState.TASK_STATE_REJECTED]) {
    await store.save(task(`t${state}`, state), context());
  }
  await store.save(task("working", TaskState.TASK_STATE_WORKING), context());
  assert.ok(await store.load(`t${TaskState.TASK_STATE_COMPLETED}`, context()), "readable right after the final save");
  await sleep(WAIT_MS * 5);
  for (const state of [TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_CANCELED, TaskState.TASK_STATE_REJECTED]) {
    assert.equal(await store.load(`t${state}`, context()), undefined, `state ${state} pruned`);
  }
  assert.ok(await store.load("working", context()), "a task that is not final is kept");
  assert.equal(store.pending, 0);
});

test("prune: a finished streaming call leaves no task in the store", async () => {
  const store = new PruningTaskStore(WAIT_MS);
  const target: Handle = async function* () {
    yield { schema_version: SCHEMA_VERSION, type: "start", request_id: CTX.request_id };
    yield { schema_version: SCHEMA_VERSION, type: "end", status: "ok" };
  };
  const card = buildAgentCard({ name: "echo-typescript", version: "0.1.0", url: "http://127.0.0.1:9000" });
  const rpc = new JsonRpcTransportHandler(buildHandler(target, card, store));
  const body = {
    jsonrpc: "2.0",
    id: 1,
    method: "SendStreamingMessage",
    params: {
      message: { role: "ROLE_USER", messageId: "m1", contextId: CTX.trace_id, parts: [{ text: "hi" }] },
      metadata: { "chassis.ctx": JSON.stringify(CTX), "chassis.input": JSON.stringify({ text: "hi", data: {} }) },
    },
  };
  const out = await rpc.handle(body, context());
  let taskId = "";
  for await (const frame of out as AsyncIterable<{ result?: Record<string, { id?: string; taskId?: string }> }>) {
    const result = frame.result ?? {};
    taskId ||= result["task"]?.id ?? result["statusUpdate"]?.taskId ?? "";
  }
  assert.ok(taskId, "the call made a task");
  await sleep(WAIT_MS * 5);
  assert.equal(await store.load(taskId, context()), undefined);
  assert.equal(store.pending, 0);
});
