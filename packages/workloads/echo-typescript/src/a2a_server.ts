// Chassis events over A2A, contract v0 (docs/contracts/contract-v0.md), in TypeScript over
// @a2a-js/sdk 1.3. The Python reference is packages/chassis/src/chassis/adapters/a2a/mapping.py
// and server.py. One request is one task. Every chassis event is one A2A event, in order, with
// the event as a JSON string under metadata["chassis.event"]; the parts are the A2A-native view.
// The request's ctx and input arrive as JSON strings too; the v0 object form is still read.
// See "Changes decided for v1 (2026-10-01)", items 1 and 4, in the contract.

import { randomUUID } from "node:crypto";

import { Role, TaskState, type AgentCard, type Message, type Part, type SendMessageRequest, type Task } from "@a2a-js/sdk";
import {
  AgentEvent,
  DefaultRequestHandler,
  InMemoryTaskStore,
  type AgentExecutionEvent,
  type AgentExecutor,
  type ExecutionEventBus,
  type RequestContext,
  type ServerCallContext,
  type TaskStore,
} from "@a2a-js/sdk/server";
import { UserBuilder, agentCardHandler, jsonRpcHandler } from "@a2a-js/sdk/server/express";
import express, { type Express } from "express";

import { bearerAuth } from "./auth.js";
import { SCHEMA_VERSION, type ChassisEvent, type Context, type Handle, type Json } from "./handle.js";
import { validateEvent } from "./schema.js";

export const EVENT_KEY = "chassis.event";
export const CTX_KEY = "chassis.ctx";
export const INPUT_KEY = "chassis.input";
export const SCHEMA_VERSION_KEY = "chassis.schema_version";
export const OUTPUT_ARTIFACT_ID = "output";
export const SUPPORTED_SCHEMA_VERSIONS = ["0"];

const textPart = (text: string): Part => ({ content: { $case: "text", value: text }, metadata: undefined, filename: "", mediaType: "" });
const dataPart = (data: unknown): Part => ({ content: { $case: "data", value: data }, metadata: undefined, filename: "", mediaType: "application/json" });
const agentMessage = (taskId: string, contextId: string, parts: Part[]): Message => ({
  messageId: randomUUID(), contextId, taskId, role: Role.ROLE_AGENT, parts, metadata: undefined, extensions: [], referenceTaskIds: [],
});
const own = (code: string, message: string): ChassisEvent => ({ schema_version: SCHEMA_VERSION, type: "error", code, message, retryable: false });

const isObject = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);

/** Thrown for a request the server cannot read; published as `a2a.bad_request`. */
export class BadRequest extends Error {}

/** A metadata value: a JSON string (v1), or the v0 object read as is. `undefined` when absent. */
function readJson(metadata: Json | undefined, key: string): Json | undefined {
  const value = metadata?.[key];
  if (value === undefined || value === null) return undefined;
  if (isObject(value)) return value; // v0 Struct form; dropped in v2
  if (typeof value !== "string") throw new BadRequest(`${key}: must be a JSON string`);
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch (exc) {
    throw new BadRequest(`${key}: not JSON: ${(exc as Error).message}`);
  }
  if (!isObject(parsed)) throw new BadRequest(`${key}: must be a JSON object`);
  return parsed;
}

/** JSON of a chassis value; throws on NaN or Infinity, which JSON.stringify would turn into null. */
export function toJson(value: unknown): string {
  return JSON.stringify(value, (key, v: unknown) => {
    if (typeof v === "number" && !Number.isFinite(v)) throw new Error(`${key || "value"}: ${v} is not JSON`);
    return v;
  });
}

/**
 * `[input, ctx]` as the workload sees them. The input is `chassis.input` when set, else the first
 * text part and the first data part (a generic A2A client). The ctx passes through unchanged.
 */
export function requestToInput(request: SendMessageRequest): [Json, Context] {
  const metadata = request.metadata as Json | undefined;
  const ctx = readJson(metadata, CTX_KEY) ?? {};
  const input = readJson(metadata, INPUT_KEY);
  if (input) return [{ ...input }, { ...ctx }];
  const parts = request.message?.parts ?? [];
  const text = parts.find((p) => p.content?.$case === "text")?.content?.value;
  const data = parts.find((p) => p.content?.$case === "data")?.content?.value as Json | undefined;
  return [{ text: typeof text === "string" && text ? text : null, data: data ?? {} }, { ...ctx }];
}

// --- Events out ---

/**
 * One chassis event as one A2A event. `append` is false on the first delta and true after.
 * Throws when the event holds NaN or Infinity.
 */
export function eventToUpdate(event: ChassisEvent, taskId: string, contextId: string, append: boolean): AgentExecutionEvent {
  const metadata = { [EVENT_KEY]: toJson(event) };
  if (event.type === "delta") {
    const artifact = { artifactId: OUTPUT_ARTIFACT_ID, name: "", description: "", parts: [textPart(String(event["text"] ?? ""))], metadata, extensions: [] };
    return AgentEvent.artifactUpdate({ taskId, contextId, artifact, append, lastChunk: false, metadata: undefined });
  }
  let state = TaskState.TASK_STATE_WORKING;
  let parts: Part[] | undefined;
  if (event.type === "tool_call") {
    const { call_id, name, arguments: args, result } = event;
    parts = [dataPart({ call_id, name, arguments: args, result })];
  } else if (event.type === "end") {
    state = TaskState.TASK_STATE_COMPLETED;
    if (event["output"] !== undefined && event["output"] !== null) parts = [dataPart(event["output"])];
  } else if (event.type === "error") {
    state = TaskState.TASK_STATE_FAILED;
    parts = [textPart(String(event["message"] ?? ""))];
  }
  const message = parts ? agentMessage(taskId, contextId, parts) : undefined;
  return AgentEvent.statusUpdate({ taskId, contextId, status: { state, message, timestamp: new Date().toISOString() }, metadata });
}

// --- The executor ---

/** Runs `handle` for one task; enforces the order: `start` first, `end` or `error` last. */
export class HandleExecutor implements AgentExecutor {
  private readonly running = new Map<string, { abort: AbortController; contextId: string }>();
  constructor(private readonly handle: Handle) {}

  execute = async (context: RequestContext, bus: ExecutionEventBus): Promise<void> => {
    const { taskId, contextId } = context;
    const history = context.request.message ? [context.request.message] : [];
    bus.publish(AgentEvent.task({ id: taskId, contextId, status: { state: TaskState.TASK_STATE_SUBMITTED, message: undefined, timestamp: new Date().toISOString() }, artifacts: [], history, metadata: undefined }));
    const publish = (event: ChassisEvent, append = false): void => bus.publish(eventToUpdate(event, taskId, contextId, append));
    const version = String(context.request.metadata?.[SCHEMA_VERSION_KEY] ?? SCHEMA_VERSION);
    if (!SUPPORTED_SCHEMA_VERSIONS.includes(version)) {
      publish(own("a2a.unsupported_schema_version", `schema version ${JSON.stringify(version)} is not supported; this server accepts ${SUPPORTED_SCHEMA_VERSIONS.join(", ")}`));
      return;
    }
    let input: Json;
    let ctx: Context;
    try {
      [input, ctx] = requestToInput(context.request);
    } catch (exc) {
      if (!(exc instanceof BadRequest)) throw exc;
      return publish(own("a2a.bad_request", exc.message)); // suggested: the code name
    }
    const abort = new AbortController();
    this.running.set(taskId, { abort, contextId });
    const stream = this.handle(input, ctx, { signal: abort.signal })[Symbol.asyncIterator]();
    let started = false;
    let seenDelta = false;
    try {
      for (;;) {
        const next = await stream.next();
        if (abort.signal.aborted) return; // cancelTask already published CANCELED
        if (next.done) return publish(own("workload.no_end", "handle ended without `end` or `error`"));
        let event: ChassisEvent;
        let update: AgentExecutionEvent;
        try {
          event = validateEvent({ schema_version: SCHEMA_VERSION, ...(next.value as Json) });
          update = eventToUpdate(event, taskId, contextId, seenDelta);
        } catch (exc) {
          return publish(own("workload.bad_event", (exc as Error).message));
        }
        if ((event.type === "start") === started) return publish(own("workload.bad_order", `${JSON.stringify(event.type)} out of order`));
        started = true;
        bus.publish(update);
        seenDelta ||= event.type === "delta";
        if (event.type === "end" || event.type === "error") return;
      }
    } catch (exc) {
      if (abort.signal.aborted) return;
      const e = exc instanceof Error ? exc : new Error(String(exc));
      publish(own("workload.exception", `${e.name}: ${e.message}`));
    } finally {
      this.running.delete(taskId);
      await stream.return?.();
    }
  };

  cancelTask = async (taskId: string, bus: ExecutionEventBus): Promise<void> => {
    const run = this.running.get(taskId);
    run?.abort.abort(); // stops the model call; execute stops at its next event
    bus.publish(AgentEvent.statusUpdate({ taskId, contextId: run?.contextId ?? "", status: { state: TaskState.TASK_STATE_CANCELED, message: undefined, timestamp: new Date().toISOString() }, metadata: undefined }));
  };
}

// --- Card and app ---

export function buildAgentCard(opts: { name: string; version: string; description?: string; url: string }): AgentCard {
  return {
    name: opts.name,
    description: opts.description || `chassis workload ${opts.name}`,
    version: opts.version,
    capabilities: { streaming: true, extensions: [] },
    defaultInputModes: ["text/plain", "application/json"],
    defaultOutputModes: ["text/plain", "application/json"],
    skills: [{ id: "handle", name: "handle", description: "handle(input, ctx) -> chassis events", tags: ["chassis"], examples: [], inputModes: [], outputModes: [], securityRequirements: [] }],
    supportedInterfaces: [{ url: opts.url.replace(/\/+$/, "") + "/", protocolBinding: "JSONRPC", tenant: "", protocolVersion: "1.0" }],
    provider: undefined,
    securitySchemes: {},
    securityRequirements: [],
    signatures: [],
  };
}

// --- The task store ---

/** suggested: how long a final task stays readable before it is forgotten, the same 3 s the
 * Python template's hidden-state test waits (`PRUNE_WAIT_S`). The wait leaves the SDK time for
 * the reads it does right after the final save. */
export const PRUNE_DELAY_MS = 3000;

const FINAL_STATES: ReadonlySet<TaskState> = new Set([
  TaskState.TASK_STATE_COMPLETED,
  TaskState.TASK_STATE_FAILED,
  TaskState.TASK_STATE_CANCELED,
  TaskState.TASK_STATE_REJECTED,
]);

/** The SDK's internal bucket for one caller scope. `TaskStore` has no delete, so this is the
 * only way to forget a task; `@a2a-js/sdk` is pinned and `test/prune.test.ts` fails if it moves. */
type Buckets = { getBucket(context: ServerCallContext): Map<string, Task> | undefined };

/** `InMemoryTaskStore` that forgets a task `delayMs` after it is saved in a final state, so a
 * finished task is not kept for the life of the process (the Python template's
 * `PruningRequestHandler` does the same). A task that never ends stays; the chassis cancels a
 * run at its `budget.timeout_ms`, which makes it final. */
export class PruningTaskStore extends InMemoryTaskStore {
  private readonly timers = new Map<string, NodeJS.Timeout>();
  constructor(private readonly delayMs: number = PRUNE_DELAY_MS) {
    super();
  }

  /** Tasks waiting to be forgotten. */
  get pending(): number {
    return this.timers.size;
  }

  override async save(task: Task, context: ServerCallContext): Promise<void> {
    await super.save(task, context);
    const state = task.status?.state;
    if (state === undefined || !FINAL_STATES.has(state)) return;
    clearTimeout(this.timers.get(task.id));
    const timer = setTimeout(() => this.forget(task.id, context), this.delayMs);
    timer.unref(); // never keeps the process (or its drain) alive
    this.timers.set(task.id, timer);
  }

  private forget(taskId: string, context: ServerCallContext): void {
    this.timers.delete(taskId);
    try {
      (this as unknown as { _scopedStore?: Buckets })._scopedStore?.getBucket(context)?.delete(taskId);
    } catch {
      // housekeeping; never fail a call over it
    }
  }
}

export function buildHandler(handle: Handle, card: AgentCard, store: TaskStore = new PruningTaskStore()): DefaultRequestHandler {
  return new DefaultRequestHandler(card, store, new HandleExecutor(handle));
}

/** The Express app (with `auth.token`, every path needs the bearer): the agent card on /.well-known/agent-card.json and JSON-RPC on `/`. */
export function buildApp(handle: Handle, card: AgentCard, auth: { token?: string | undefined; previous?: string | undefined } = {}): Express {
  const handler = buildHandler(handle, card);
  const app = express();
  // First, so the agent card is protected too and the app never sees the credential.
  if (auth.token) app.use(bearerAuth(auth.token, auth.previous));
  app.use("/.well-known/agent-card.json", agentCardHandler({ agentCardProvider: handler }));
  app.use("/", jsonRpcHandler({ requestHandler: handler, userBuilder: UserBuilder.noAuthentication }));
  return app;
}
