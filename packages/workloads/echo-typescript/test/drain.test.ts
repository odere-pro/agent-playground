// Drain on SIGTERM: in-flight calls finish, idle keep-alive connections close, and a call that
// outlives DRAIN_TIMEOUT_MS forces exit 1. A real HTTP server on a Unix socket in a temp folder,
// no TCP. `exit` is a stub, so nothing here ends the test process.

import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { Agent, createServer, request, type Server, type ServerResponse } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { DEFAULT_DRAIN_TIMEOUT_MS, drain, drainOnSignals, drainTimeoutMs } from "../src/drain.js";

interface Served {
  server: Server;
  path: string;
  agent: Agent;
  pending: ServerResponse[];
  cleanup: () => void;
}

/** Answers `/fast` at once; holds `/slow` open in `pending` until the test ends it. */
async function serve(): Promise<Served> {
  const folder = mkdtempSync(join(tmpdir(), "ts-drain-"));
  const path = join(folder, "a2a.sock");
  const pending: ServerResponse[] = [];
  const server = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/plain" });
    if (req.url === "/slow") {
      res.write("start ");
      pending.push(res);
    } else res.end("ok");
  });
  await new Promise<void>((resolve) => server.listen(path, resolve));
  const agent = new Agent({ keepAlive: true });
  const cleanup = (): void => {
    agent.destroy();
    server.closeAllConnections();
    server.close();
    rmSync(folder, { recursive: true, force: true });
  };
  return { server, path, agent, pending, cleanup };
}

/** GET over the socket with the keep-alive agent; resolves with the body once it ends. */
function get(s: Served, url: string, onHeaders?: () => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const req = request({ socketPath: s.path, path: url, agent: s.agent }, (res) => {
      onHeaders?.();
      let body = "";
      res.on("data", (chunk: Buffer) => (body += chunk.toString()));
      res.on("end", () => resolve(body));
      res.on("error", reject);
    });
    req.on("error", reject);
    req.end();
  });
}

function exitSpy(): { exit: (code: number) => void; code: Promise<number> } {
  let exit!: (code: number) => void;
  const code = new Promise<number>((resolve) => (exit = resolve));
  return { exit, code };
}

function within<T>(ms: number, promise: Promise<T>): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => setTimeout(() => reject(new Error(`not within ${ms} ms`)), ms).unref()),
  ]);
}

test("drain: an idle keep-alive connection does not hold the exit", async () => {
  const s = await serve();
  try {
    assert.equal(await get(s, "/fast"), "ok"); // the agent keeps this connection open
    const spy = exitSpy();
    drain(s.server, 10_000, spy.exit);
    assert.equal(await within(1000, spy.code), 0);
  } finally {
    s.cleanup();
  }
});

test("drain: an in-flight call finishes, then exit 0", async () => {
  const s = await serve();
  try {
    let headers!: () => void;
    const started = new Promise<void>((resolve) => (headers = resolve));
    const body = get(s, "/slow", headers);
    await started;
    const spy = exitSpy();
    let exited = false;
    void spy.code.then(() => (exited = true));
    drain(s.server, 10_000, spy.exit);
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(exited, false, "exit waits for the in-flight call");
    s.pending[0]?.end("finished");
    assert.equal(await body, "start finished");
    assert.equal(await within(1000, spy.code), 0);
  } finally {
    s.cleanup();
  }
});

test("drain: a call that outlives the drain timeout forces exit 1", async () => {
  const s = await serve();
  try {
    let headers!: () => void;
    const started = new Promise<void>((resolve) => (headers = resolve));
    void get(s, "/slow", headers).catch(() => undefined);
    await started;
    const spy = exitSpy();
    drain(s.server, 200, spy.exit);
    assert.equal(await within(2000, spy.code), 1);
  } finally {
    s.cleanup();
  }
});

test("drainOnSignals: the first SIGTERM drains, a second one exits 1 at once", async () => {
  const s = await serve();
  const before = process.listenerCount("SIGTERM");
  const codes: number[] = [];
  const stop = drainOnSignals(s.server, 10_000, (code) => codes.push(code));
  try {
    let headers!: () => void;
    const started = new Promise<void>((resolve) => (headers = resolve));
    void get(s, "/slow", headers).catch(() => undefined);
    await started;
    process.emit("SIGTERM", "SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.deepEqual(codes, [], "the first signal waits for the in-flight call");
    process.emit("SIGTERM", "SIGTERM");
    assert.deepEqual(codes, [1]);
  } finally {
    stop();
    s.cleanup();
  }
  assert.equal(process.listenerCount("SIGTERM"), before);
});

test("drainTimeoutMs: DRAIN_TIMEOUT_MS, default 30000, refuses a bad value", () => {
  assert.equal(DEFAULT_DRAIN_TIMEOUT_MS, 30_000);
  assert.equal(drainTimeoutMs({}), 30_000);
  assert.equal(drainTimeoutMs({ DRAIN_TIMEOUT_MS: "1500" }), 1500);
  assert.throws(() => drainTimeoutMs({ DRAIN_TIMEOUT_MS: "soon" }), /DRAIN_TIMEOUT_MS/);
  assert.throws(() => drainTimeoutMs({ DRAIN_TIMEOUT_MS: "-1" }), /DRAIN_TIMEOUT_MS/);
});
