// The simplifier, ported from echo-python: one model call over OpenAI-compatible HTTP, streamed
// back as chassis events (docs/contracts/contract-v0.md). Plain objects in, event objects out,
// `schema_version: "0"` on each. The model call goes to CHASSIS_MODEL_URL, the chassis's model
// proxy. No model key lives here. In the remote lane CHASSIS_API_TOKEN, when set and not empty, goes
// out as `Authorization: Bearer <token>` on the model call (read from the environment on each call,
// never logged or put in an event); unset, no Authorization header is sent. The input text is its own user
// message, never merged into the system prompt. `ctx.traceparent`, when set, goes out on the
// model call as is (contract v1 item 4). With CHASSIS_MODEL_UDS set, the model call goes over that
// Unix socket instead of TCP (the URL then only fills the path and the Host header).

import { request as httpRequest, type IncomingMessage } from "node:http";
import { Readable } from "node:stream";

export const SCHEMA_VERSION = "0";
export const PROMPT_VERSION = "simplifier-v1";
export const SYSTEM_PROMPT = "Rewrite in plain words. Short sentences. Keep every fact.";
const DEFAULT_ROUTE = "big-default";
const DEFAULT_MODEL_URL = "http://127.0.0.1:8090/v1";
const DEFAULT_TIMEOUT_MS = 30_000;

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

/** `start`, one `delta` per content chunk, `metrics` from the final chunk, `end`; or `error`. */
export async function* handle(input: Json, ctx: Context, deps: Deps = {}): AsyncGenerator<ChassisEvent> {
  yield event("start", { request_id: String(ctx["request_id"] ?? "") });
  const route = String(ctx["model_route"] || DEFAULT_ROUTE);
  const base = (deps.modelUrl ?? process.env["CHASSIS_MODEL_URL"] ?? DEFAULT_MODEL_URL).replace(/\/+$/, "");
  const headers: Record<string, string> = { "content-type": "application/json" };
  const traceparent = ctx["traceparent"];
  if (typeof traceparent === "string" && traceparent) headers["traceparent"] = traceparent;
  const token = process.env["CHASSIS_API_TOKEN"];
  if (token) headers["authorization"] = `Bearer ${token}`;
  const body = {
    model: route,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: String(input["text"] ?? "") },
    ],
    stream: true,
    stream_options: { include_usage: true },
  };
  let usage = [0, 0];
  try {
    const uds = process.env["CHASSIS_MODEL_UDS"];
    const send = deps.fetch ?? (uds ? udsFetch(uds) : fetch);
    const response = await send(`${base}/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: deps.signal ? AbortSignal.any([deps.signal, AbortSignal.timeout(timeoutMs(ctx))]) : AbortSignal.timeout(timeoutMs(ctx)),
    });
    if (response.status >= 400) {
      const detail = (await response.text()).slice(0, 200);
      yield error(`http_${response.status}`, detail || response.statusText, response.status >= 500);
      return;
    }
    if (!response.body) {
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
        yield error("bad_response", "bad SSE payload", false);
        return;
      }
      const err = chunk["error"] as Json | undefined;
      if (err && typeof err === "object") {
        yield error(String(err["code"] || "model_error"), String(err["message"] || "model error"), Boolean(err["retryable"]));
        return;
      }
      const u = chunk["usage"] as Json | undefined;
      if (u) usage = [int(u["prompt_tokens"]), int(u["completion_tokens"])];
      for (const choice of (chunk["choices"] as Json[] | undefined) ?? []) {
        const content = (choice["delta"] as Json | undefined)?.["content"];
        if (typeof content === "string" && content) yield event("delta", { text: content });
      }
    }
  } catch (exc) {
    const e = exc instanceof Error ? exc : new Error(String(exc));
    if (e.name === "TimeoutError") yield error("timeout", e.message || "model call timed out", true);
    else yield error("connect_error", e.message || e.name, true);
    return;
  }
  yield event("metrics", { input_tokens: usage[0], output_tokens: usage[1], model_route: route, attempt: 1 });
  yield event("end", { status: "ok" });
}
