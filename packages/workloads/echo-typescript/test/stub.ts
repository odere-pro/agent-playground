// A stubbed `fetch` that answers like the fake model server: an SSE body with one chunk per
// content delta, a final chunk with `usage`, then `[DONE]`. It records every request it gets.

export interface Recorded {
  url: string;
  headers: Headers;
  body: Record<string, unknown>;
}

export function sseBody(pieces: string[], usage = { prompt_tokens: 12, completion_tokens: 3 }): string {
  const chunks = pieces.map((content) => ({ choices: [{ index: 0, delta: { content } }] }));
  chunks.push({ choices: [], usage } as never);
  return chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n";
}

export function stubFetch(
  status: number,
  body: string,
): { fetch: typeof fetch; calls: Recorded[] } {
  const calls: Recorded[] = [];
  const fake = async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    // The tool endpoint is unreachable here: no tools are offered and no model call is recorded.
    if (String(url).endsWith("/mcp")) throw new TypeError("fetch failed");
    calls.push({
      url: String(url),
      headers: new Headers(init?.headers),
      body: JSON.parse(String(init?.body)) as Record<string, unknown>,
    });
    const type = status < 400 ? "text/event-stream" : "text/plain";
    return new Response(body, { status, headers: { "content-type": type } });
  };
  return { fetch: fake as typeof fetch, calls };
}

export const CTX = {
  request_id: "01J9ZK3W1N2M7Q8R",
  trace_id: "4bf92f3577b34da6a3ce929d0e0e4736",
  idempotency_key: "k-1",
  agent: "simplifier",
  agent_version: "0.1.0",
  model_route: "local-small",
  budget: { max_tokens: 2000, timeout_ms: 5000 },
  versions: { chassis: "0.1.0", prompt: "simplifier-v1" },
};

/** One SSE chunk that streams (part of) a tool call, as an OpenAI-compatible server does. */
export function sseToolCall(
  index: number,
  call: { id?: string; name?: string; arguments?: string },
): Json {
  return {
    choices: [
      {
        index: 0,
        delta: {
          tool_calls: [
            {
              index,
              ...(call.id ? { id: call.id, type: "function" } : {}),
              function: {
                ...(call.name ? { name: call.name } : {}),
                ...(call.arguments !== undefined ? { arguments: call.arguments } : {}),
              },
            },
          ],
        },
      },
    ],
  };
}

type Json = Record<string, unknown>;

/** An SSE body from raw chunks, then a usage chunk and `[DONE]`. */
export function sseChunks(chunks: Json[], usage = { prompt_tokens: 12, completion_tokens: 3 }): string {
  return [...chunks, { choices: [], usage }].map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n";
}

/** A model that answers call n with `bodies[n]` (the last one repeats) and records each request. */
export function scriptedModel(bodies: string[]): { fetch: typeof fetch; calls: Recorded[] } {
  const calls: Recorded[] = [];
  const fake = async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    calls.push({
      url: String(url),
      headers: new Headers(init?.headers),
      body: JSON.parse(String(init?.body)) as Record<string, unknown>,
    });
    const body = bodies[Math.min(calls.length - 1, bodies.length - 1)] ?? "";
    return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
  };
  return { fetch: fake as typeof fetch, calls };
}

export interface McpRequest {
  method: string;
  headers: Headers;
  rpc: Json | undefined;
}

/** A stateless MCP server over a stubbed `fetch`: initialize, list, call. `tools` maps a tool name
 * to its answer; a function answer sees the arguments, a thrown error is an `isError` result. */
export function mcpStub(
  tools: Record<string, (args: Json) => unknown>,
  opts: { transportError?: boolean } = {},
): { fetch: typeof fetch; requests: McpRequest[] } {
  const requests: McpRequest[] = [];
  const reply = (id: unknown, result: Json): Response =>
    new Response(JSON.stringify({ jsonrpc: "2.0", id, result }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  const fake = async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? "GET";
    const rpc = init?.body ? (JSON.parse(String(init.body)) as Json) : undefined;
    requests.push({ method, headers: new Headers(init?.headers), rpc });
    if (method !== "POST" || !rpc) return new Response(null, { status: 405 });
    const id = rpc["id"];
    if (id === undefined) return new Response(null, { status: 202 });
    const params = (rpc["params"] ?? {}) as Json;
    switch (rpc["method"]) {
      case "initialize":
        return reply(id, {
          protocolVersion: params["protocolVersion"],
          capabilities: { tools: {} },
          serverInfo: { name: "stub-chassis", version: "0" },
        });
      case "tools/list":
        return reply(id, {
          tools: Object.keys(tools).map((name) => ({
            name,
            description: `stub ${name}`,
            inputSchema: { type: "object", properties: {} },
          })),
        });
      case "tools/call": {
        if (opts.transportError) return new Response("boom", { status: 500 });
        const name = String(params["name"]);
        try {
          const answer = tools[name]?.((params["arguments"] ?? {}) as Json);
          return reply(id, { content: [{ type: "text", text: JSON.stringify(answer) }], structuredContent: answer });
        } catch (exc) {
          return reply(id, { content: [{ type: "text", text: String(exc) }], isError: true });
        }
      }
      default:
        return new Response(null, { status: 400 });
    }
  };
  return { fetch: fake as typeof fetch, requests };
}

/** The benchmark tools, stubbed. */
export const GLOSSARY = "A small language model: a model small enough to run cheaply on one GPU or a CPU.";
export const benchmarkTools = {
  glossary_lookup: (args: Json) => ({ term: args["term"], definition: GLOSSARY }),
  acronym_expand: (args: Json) => ({
    acronym: args["acronym"],
    expansion: args["acronym"] === "RAG" ? "retrieval-augmented generation" : null,
  }),
};

/** A tool endpoint that refuses the connection, so a test opens no TCP socket. */
export const noTools = (async () => {
  throw new TypeError("fetch failed");
}) as typeof fetch;
