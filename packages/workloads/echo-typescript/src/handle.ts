// The simplifier, ported from echo-python: OpenAI-compatible HTTP to the chassis's model proxy,
// streamed back as chassis events (docs/contracts/contract-v0.md), with the chassis's tools over
// MCP in a small hand-written loop (`./tools.ts`). Plain objects in, event objects out,
// `schema_version: "0"` on each. The model call goes to CHASSIS_MODEL_URL, the tools to
// CHASSIS_TOOL_URL. No model key lives here. In the remote lane CHASSIS_API_TOKEN, when set and
// not empty, goes out as `Authorization: Bearer <token>` on every model and MCP call (read from
// the environment on each run, never logged or put in an event); unset, no Authorization header.
// `ctx.traceparent`, when set, goes out as the `traceparent` header on every model and MCP call.
// The input text is its own user message, never merged into the system prompt. With
// CHASSIS_MODEL_UDS set, the model call goes over that Unix socket instead of TCP (the URL then
// only fills the path and the Host header).

import { request as httpRequest, type IncomingMessage } from "node:http";
import { Readable } from "node:stream";

import * as tools from "./tools.js";

export const SCHEMA_VERSION = "0";
export const PROMPT_VERSION = "simplifier-v1";
export const SYSTEM_PROMPT = "Rewrite in plain words. Short sentences. Keep every fact.";
const DEFAULT_ROUTE = "big-default";
const DEFAULT_MODEL_URL = "http://127.0.0.1:8090/v1";
const DEFAULT_TIMEOUT_MS = 30_000;
// suggested: at most 3 rounds of tool calls per run; a 4th ask is `tool_loop_exceeded`.
export const MAX_TOOL_ROUNDS = 3;

export type Json = Record<string, unknown>;
export type ChassisEvent = Json & { schema_version: string; type: string };
/** `context.v0.json` as plain JSON; `traceparent` is the run's W3C value, optional. */
export type Context = Json & { traceparent?: string | null };
export type Handle = (input: Json, ctx: Context, deps?: Deps) => AsyncIterable<ChassisEvent>;

/** What `handle` takes besides the contract's two arguments; never data, never on the wire. */
export interface Deps {
  /** Tests stub it. */
  fetch?: typeof fetch;
  /** Defaults to CHASSIS_MODEL_URL, then the chassis proxy on localhost. */
  modelUrl?: string;
  /** Aborted by the server on cancel; the model call stops. */
  signal?: AbortSignal;
  /** The MCP calls use this; defaults to `fetch` above, then the global one. Tests stub it. */
  toolFetch?: typeof fetch;
  /** Defaults to CHASSIS_TOOL_URL, then the chassis on localhost. */
  toolUrl?: string;
}

const event = (type: string, fields: Json = {}): ChassisEvent => ({
  schema_version: SCHEMA_VERSION,
  type,
  ...fields,
});
const error = (code: string, message: string, retryable: boolean): ChassisEvent =>
  event("error", { code, message, retryable });
const int = (v: unknown): number => (typeof v === "number" ? Math.trunc(v) : 0);

function timeoutMs(ctx: Json): number {
  const budget = ctx["budget"] as Json | undefined;
  const ms = budget?.["timeout_ms"];
  return typeof ms === "number" && ms > 0 ? ms : DEFAULT_TIMEOUT_MS;
}

async function* lines(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const decoder = new TextDecoder();
  let buffer = "";
  for await (const chunk of body) {
    buffer += decoder.decode(chunk, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n")) >= 0) {
      yield buffer.slice(0, cut).replace(/\r$/, "");
      buffer = buffer.slice(cut + 1);
    }
  }
  if (buffer) yield buffer;
}

/**
 * A `fetch` over the Unix socket at `socketPath`. Node's built-in `fetch` takes no socket path
 * (its undici `Agent` is not exported), so this goes through `http.request`. It covers what the
 * model call uses: method, headers, a string body, and the abort signal. The body streams; an
 * abort rejects, or errors the body, with `signal.reason`, as `fetch` does.
 */
export function udsFetch(socketPath: string): typeof fetch {
  const call = (url: string | URL | Request, init: RequestInit = {}): Promise<Response> =>
    new Promise<Response>((resolve, reject) => {
      const target = new URL(url instanceof Request ? url.url : String(url));
      const signal = init.signal ?? undefined;
      if (signal?.aborted) return reject(signal.reason as Error);
      const headers: Record<string, string> = { host: target.host };
      new Headers(init.headers).forEach((value, key) => (headers[key] = value));
      let res: IncomingMessage | undefined;
      const req = httpRequest({ socketPath, method: init.method ?? "GET", path: target.pathname + target.search, headers }, (incoming) => {
        res = incoming;
        const out = new Headers();
        for (const [key, value] of Object.entries(incoming.headers)) {
          if (value !== undefined) out.set(key, Array.isArray(value) ? value.join(", ") : value);
        }
        const status = incoming.statusCode ?? 502;
        const body = status === 204 || status === 304 ? null : (Readable.toWeb(incoming) as ReadableStream<Uint8Array>);
        resolve(new Response(body, { status, statusText: incoming.statusMessage ?? "", headers: out }));
      });
      const abort = (): void => {
        const reason = signal?.reason as Error;
        res?.destroy(reason);
        req.destroy(reason);
      };
      signal?.addEventListener("abort", abort, { once: true });
      req.on("close", () => signal?.removeEventListener("abort", abort));
      req.on("error", (err) => reject(signal?.aborted ? (signal.reason as Error) : err));
      if (init.body !== undefined && init.body !== null) req.write(String(init.body));
      req.end();
    });
  return call as typeof fetch;
}

/** What one streamed model call left behind besides its deltas. */
interface Turn {
  usage: [number, number];
  calls: Map<number, tools.ToolCall>;
  failed: boolean;
}

/** One streamed model call: `delta` events, or one `error` (and `turn.failed`). */
async function* stream(send: typeof fetch, url: string, init: RequestInit, turn: Turn): AsyncGenerator<ChassisEvent> {
  const response = await send(url, init);
  if (response.status >= 400) {
    const detail = (await response.text()).slice(0, 200);
    turn.failed = true;
    yield error(`http_${response.status}`, detail || response.statusText, response.status >= 500);
    return;
  }
  if (!response.body) {
    turn.failed = true;
    yield error("bad_response", "empty response body", false);
    return;
  }
  for await (const line of lines(response.body)) {
    if (!line.startsWith("data:")) continue;
    const payload = line.slice(5).trim();
    if (payload === "[DONE]") break;
    let chunk: Json;
    try {
      chunk = JSON.parse(payload) as Json;
    } catch {
      turn.failed = true;
      yield error("bad_response", "bad SSE payload", false);
      return;
    }
    const err = chunk["error"] as Json | undefined;
    if (err && typeof err === "object") {
      turn.failed = true;
      yield error(String(err["code"] || "model_error"), String(err["message"] || "model error"), Boolean(err["retryable"]));
      return;
    }
    const u = chunk["usage"] as Json | undefined;
    if (u) turn.usage = [int(u["prompt_tokens"]), int(u["completion_tokens"])];
    for (const choice of (chunk["choices"] as Json[] | undefined) ?? []) {
      const delta = (choice["delta"] as Json | undefined) ?? {};
      const pieces = delta["tool_calls"];
      if (Array.isArray(pieces)) tools.addDeltas(turn.calls, pieces as Json[]);
      const content = delta["content"];
      if (typeof content === "string" && content) yield event("delta", { text: content });
    }
  }
}

/**
 * `start`, then per model call its `delta`s and a `tool_call` per tool it asked for, then one
 * `metrics` summing every call, `end`; or `error`. Event order and codes match echo-python.
 */
export async function* handle(input: Json, ctx: Context, deps: Deps = {}): AsyncGenerator<ChassisEvent> {
  yield event("start", { request_id: String(ctx["request_id"] ?? "") });
  const route = String(ctx["model_route"] || DEFAULT_ROUTE);
  const base = (deps.modelUrl ?? process.env["CHASSIS_MODEL_URL"] ?? DEFAULT_MODEL_URL).replace(/\/+$/, "");
  const headers: Record<string, string> = {};
  const traceparent = ctx["traceparent"];
  if (typeof traceparent === "string" && traceparent) headers["traceparent"] = traceparent;
  const token = process.env["CHASSIS_API_TOKEN"];
  if (token) headers["authorization"] = `Bearer ${token}`;
  const timeout = timeoutMs(ctx);
  const uds = process.env["CHASSIS_MODEL_UDS"];
  const send = deps.fetch ?? (uds ? udsFetch(uds) : fetch);
  const mcp: tools.McpOptions = {
    fetch: deps.toolFetch ?? deps.fetch ?? fetch,
    headers,
    timeoutMs: timeout,
    signal: deps.signal,
    ...(deps.toolUrl !== undefined ? { url: deps.toolUrl } : {}),
  };
  const offered = await tools.listOpenAiTools(mcp);
  const messages: Json[] = [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user", content: String(input["text"] ?? "") },
  ];
  let tokensIn = 0;
  let tokensOut = 0;
  try {
    for (let rounds = 0; rounds <= MAX_TOOL_ROUNDS; rounds++) {
      const body: Json = { model: route, messages, stream: true, stream_options: { include_usage: true } };
      if (offered.length) body["tools"] = offered;
      const turn: Turn = { usage: [0, 0], calls: new Map(), failed: false };
      const init: RequestInit = {
        method: "POST",
        headers: { "content-type": "application/json", ...headers },
        body: JSON.stringify(body),
        signal: deps.signal ? AbortSignal.any([deps.signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout),
      };
      yield* stream(send, `${base}/chat/completions`, init, turn);
      if (turn.failed) return;
      tokensIn += turn.usage[0];
      tokensOut += turn.usage[1];
      if (turn.calls.size === 0) break;
      if (rounds === MAX_TOOL_ROUNDS) {
        yield error("tool_loop_exceeded", `the model asked for tools more than ${MAX_TOOL_ROUNDS} times`, false);
        return;
      }
      const calls = [...turn.calls.entries()].sort((a, b) => a[0] - b[0]).map(([, c]) => c);
      messages.push(tools.assistantMessage(calls));
      for (const call of calls) {
        let args: Json;
        let result: Json;
        try {
          args = tools.parseArguments(call);
        } catch (exc) {
          yield error("bad_response", exc instanceof Error ? exc.message : String(exc), false);
          return;
        }
        try {
          result = await tools.callTool(call.name, args, mcp);
        } catch (exc) {
          yield error("tool_error", exc instanceof Error ? exc.message : String(exc), false);
          return;
        }
        yield event("tool_call", { call_id: call.callId, name: call.name, arguments: args, result });
        messages.push(tools.toolMessage(call.callId, result));
      }
    }
  } catch (exc) {
    const e = exc instanceof Error ? exc : new Error(String(exc));
    if (e.name === "TimeoutError") yield error("timeout", e.message || "model call timed out", true);
    else yield error("connect_error", e.message || e.name, true);
    return;
  }
  yield event("metrics", { input_tokens: tokensIn, output_tokens: tokensOut, model_route: route, attempt: 1 });
  yield event("end", { status: "ok" });
}
