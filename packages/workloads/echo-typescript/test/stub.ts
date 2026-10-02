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
