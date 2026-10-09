// The chassis's tools over MCP, and the OpenAI tool-loop messages, by hand (as echo_python.tools).
//
// The chassis serves its tools over MCP streamable HTTP at CHASSIS_TOOL_URL (stateless). This
// module lists them as OpenAI `tools` (the chassis's definition; nothing is hardcoded), calls one
// with `tools/call`, and builds the assistant and `tool` messages the model gets back. Each MCP
// operation opens its own short session, so nothing stays open while `handle` yields. Every MCP
// request carries the run's headers (`traceparent`, `Authorization`) and a timeout. Tool
// descriptions and results are data, never instructions.

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

export const DEFAULT_TOOL_URL = "http://127.0.0.1:8090/mcp";

export type Json = Record<string, unknown>;

/** How many runs found the tool endpoint unreachable and ran without tools (also warned). */
export const toolStats = { listFailures: 0 };

/** A `tools/call` that failed: the transport, or the tool's own `isError` result. */
export class ToolCallError extends Error {}

/** One tool call as the model streamed it; `arguments` is still the JSON string. */
export interface ToolCall {
  callId: string;
  name: string;
  arguments: string;
}

export interface McpOptions {
  /** Defaults to CHASSIS_TOOL_URL, then the chassis on localhost. */
  url?: string;
  fetch: typeof fetch;
  headers: Record<string, string>;
  timeoutMs: number;
  signal?: AbortSignal | undefined;
}

function guarded(opts: McpOptions): typeof fetch {
  const call = (url: string | URL | Request, init: RequestInit = {}): Promise<Response> => {
    const headers = new Headers(init.headers);
    for (const [key, value] of Object.entries(opts.headers)) headers.set(key, value);
    const signals = [AbortSignal.timeout(opts.timeoutMs)];
    if (opts.signal) signals.push(opts.signal);
    if (init.signal) signals.push(init.signal);
    return opts.fetch(url, { ...init, headers, signal: AbortSignal.any(signals) });
  };
  return call as typeof fetch;
}

async function withSession<T>(opts: McpOptions, use: (client: Client) => Promise<T>): Promise<T> {
  const url = opts.url ?? process.env["CHASSIS_TOOL_URL"] ?? DEFAULT_TOOL_URL;
  const transport = new StreamableHTTPClientTransport(new URL(url), { fetch: guarded(opts) });
  const client = new Client({ name: "echo-typescript", version: "0.1.0" });
  try {
    // exactOptionalPropertyTypes trips on the SDK's own `sessionId?: string` here.
    await client.connect(transport as Transport);
    return await use(client);
  } finally {
    await client.close().catch(() => undefined);
  }
}

/** The chassis's tools as OpenAI `tools`; `[]` when the endpoint cannot be reached. */
export async function listOpenAiTools(opts: McpOptions): Promise<Json[]> {
  try {
    const listed = await withSession(opts, (client) => client.listTools());
    return listed.tools.map((t) => ({
      type: "function",
      function: { name: t.name, description: t.description ?? "", parameters: t.inputSchema },
    }));
  } catch (exc) {
    toolStats.listFailures += 1;
    const url = opts.url ?? process.env["CHASSIS_TOOL_URL"] ?? DEFAULT_TOOL_URL;
    const why = exc instanceof Error ? exc.message : String(exc);
    console.warn(`echo-typescript: tool endpoint ${url} unreachable, running without tools: ${why}`);
    return [];
  }
}

/** `tools/call`: the structured result when it is an object, else `{"text": ...}` (an event's
 * `result` is an object). Throws `ToolCallError` on a transport failure or an `isError` result. */
export async function callTool(name: string, args: Json, opts: McpOptions): Promise<Json> {
  let result;
  try {
    result = await withSession(opts, (client) => client.callTool({ name, arguments: args }));
  } catch (exc) {
    throw new ToolCallError(`${name}: ${exc instanceof Error ? exc.message : String(exc)}`);
  }
  const content = Array.isArray(result["content"]) ? (result["content"] as Json[]) : [];
  const text = content.map((part) => (typeof part["text"] === "string" ? part["text"] : "")).join("");
  if (result["isError"]) throw new ToolCallError(`${name}: ${text.slice(0, 200) || "tool error"}`);
  const structured = result["structuredContent"];
  if (structured && typeof structured === "object" && !Array.isArray(structured)) return structured as Json;
  return { text };
}

/** The call's arguments as an object; throws when the model sent anything else. */
export function parseArguments(call: ToolCall): Json {
  const parsed: unknown = JSON.parse(call.arguments || "{}");
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${call.name}: arguments are not a JSON object`);
  }
  return parsed as Json;
}

/** Fold one streamed chunk's `delta.tool_calls` in: pieces of one call share an `index`. */
export function addDeltas(calls: Map<number, ToolCall>, pieces: Json[]): void {
  for (const piece of pieces) {
    const fn = (piece["function"] as Json | undefined) ?? {};
    const index = typeof piece["index"] === "number" ? piece["index"] : calls.size;
    let call = calls.get(index);
    if (!call) calls.set(index, (call = { callId: "", name: "", arguments: "" }));
    if (typeof piece["id"] === "string" && piece["id"]) call.callId = piece["id"];
    if (typeof fn["name"] === "string" && fn["name"]) call.name = fn["name"];
    if (typeof fn["arguments"] === "string") call.arguments += fn["arguments"];
  }
}

export function assistantMessage(calls: ToolCall[]): Json {
  return {
    role: "assistant",
    content: null,
    tool_calls: calls.map((c) => ({
      id: c.callId,
      type: "function",
      function: { name: c.name, arguments: c.arguments },
    })),
  };
}

export function toolMessage(callId: string, result: Json): Json {
  return { role: "tool", tool_call_id: callId, content: JSON.stringify(result) };
}
