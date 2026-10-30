// Test-only preload for the TypeScript agent (`node --import`): sends its MCP calls over a Unix
// socket. The agent's tool client uses the global `fetch` and has no socket option (only its
// model call has `CHASSIS_MODEL_UDS`), and the offline gate allows Unix sockets only. Requests to
// the host named by POC06A_TOOL_HOST go over POC06A_TOOL_UDS, through the agent's own `udsFetch`;
// every other request is left alone. The workload code is not touched.
const socket = process.env.POC06A_TOOL_UDS;
const host = process.env.POC06A_TOOL_HOST;
const module_url = process.env.POC06A_UDS_FETCH_MODULE;
if (socket && host && module_url) {
  const { udsFetch } = await import(module_url);
  const viaSocket = udsFetch(socket);
  const original = globalThis.fetch;
  globalThis.fetch = (input, init) => {
    const target = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
    return target.hostname === host ? viaSocket(input, init) : original(input, init);
  };
}
