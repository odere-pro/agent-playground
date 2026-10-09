// The inbound bearer check (`--require-token-env`, `--previous-token-env`), as workload_a2a's:
// the app is driven through an in-process Express handler on a Unix socket in a temp folder (no
// TCP); the start-up errors run the real entry point and expect exit 2 before it listens.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { request, type Server } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { buildAgentCard, buildApp } from "../src/a2a_server.js";
import { bearerAllowed, parseServeOptions, StartupError } from "../src/auth.js";
import { handle } from "../src/handle.js";
import { bindHost } from "../src/host.js";
import { CTX, mcpStub, scriptedModel, sseBody } from "./stub.js";

const TOKEN = "tok-inbound-0123456789";
const OLD = "tok-previous-9876543210";
const card = buildAgentCard({ name: "echo-typescript", version: "0.1.0", url: "http://127.0.0.1:9000" });

interface Reply {
  status: number;
  headers: Record<string, string | string[] | undefined>;
  body: string;
}

async function withApp<T>(
  auth: { token?: string; previous?: string },
  appHandle: Parameters<typeof buildApp>[0],
  use: (call: (path: string, headers?: Record<string, string>, body?: string) => Promise<Reply>) => Promise<T>,
): Promise<T> {
  const folder = mkdtempSync(join(tmpdir(), "ts-auth-"));
  const socketPath = join(folder, "app.sock");
  const server: Server = buildApp(appHandle, card, auth).listen(socketPath);
  await new Promise((resolve) => server.once("listening", resolve));
  const call = (path: string, headers: Record<string, string> = {}, body?: string): Promise<Reply> =>
    new Promise((resolve, reject) => {
      const req = request({ socketPath, path, method: body === undefined ? "GET" : "POST", headers }, (res) => {
        let text = "";
        res.on("data", (chunk: Buffer) => (text += chunk.toString()));
        res.on("end", () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body: text }));
      });
      req.on("error", reject);
      req.end(body);
    });
  try {
    return await use(call);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    rmSync(folder, { recursive: true, force: true });
  }
}

const CARD = "/.well-known/agent-card.json";
const bearer = (token: string): Record<string, string> => ({ authorization: `Bearer ${token}` });
const never = async function* (): AsyncGenerator<never> {};

test("no token: 401 with the fixed body and www-authenticate, on the card and on JSON-RPC", async () => {
  await withApp({ token: TOKEN }, never, async (call) => {
    for (const reply of [await call(CARD), await call("/", { "content-type": "application/json" }, "{}")]) {
      assert.equal(reply.status, 401);
      assert.equal(reply.body, '{"error":"unauthorized"}');
      assert.equal(reply.headers["www-authenticate"], "Bearer");
      assert.match(String(reply.headers["content-type"]), /application\/json/);
    }
  });
});

test("a wrong token, a wrong scheme, and an empty bearer all get the same 401", async () => {
  await withApp({ token: TOKEN }, never, async (call) => {
    const bad = [bearer("nope"), bearer(`${TOKEN}x`), bearer(TOKEN.slice(1)), { authorization: `Basic ${TOKEN}` }, { authorization: "Bearer " }, { authorization: "Bearer" }, { authorization: TOKEN }];
    const first = await call(CARD, bearer("nope"));
    for (const headers of bad) {
      const reply = await call(CARD, headers);
      assert.deepEqual([reply.status, reply.body], [401, first.body], JSON.stringify(headers));
    }
  });
});

test("the right token: 200 on the card; the scheme is case-insensitive", async () => {
  await withApp({ token: TOKEN }, never, async (call) => {
    const reply = await call(CARD, bearer(TOKEN));
    assert.equal(reply.status, 200);
    assert.equal((JSON.parse(reply.body) as { name: string }).name, "echo-typescript");
    assert.equal((await call(CARD, { authorization: `bearer ${TOKEN}` })).status, 200);
  });
});

test("the previous token is accepted too, and the old one stops with no previous", async () => {
  await withApp({ token: TOKEN, previous: OLD }, never, async (call) => {
    assert.equal((await call(CARD, bearer(TOKEN))).status, 200);
    assert.equal((await call(CARD, bearer(OLD))).status, 200);
    assert.equal((await call(CARD, bearer("other"))).status, 401);
  });
  await withApp({ token: TOKEN }, never, async (call) => {
    assert.equal((await call(CARD, bearer(OLD))).status, 401);
  });
});

test("no token configured: no check", async () => {
  await withApp({}, never, async (call) => {
    assert.equal((await call(CARD)).status, 200);
  });
});

test("the authorization header is stripped before the A2A app sees it", async () => {
  // The JSON-RPC route runs `handle` with a ctx read from the body; the SDK's call context holds
  // the request headers. Spy on them through a request handler placed after the check.
  const { default: express } = await import("express");
  const { bearerAuth } = await import("../src/auth.js");
  const seen: { headers: unknown; raw: string[] }[] = [];
  const app = express();
  app.use(bearerAuth(TOKEN));
  app.use((req, res) => {
    seen.push({ headers: { ...req.headers }, raw: [...req.rawHeaders] });
    res.json({ ok: true });
  });
  const folder = mkdtempSync(join(tmpdir(), "ts-auth-"));
  const socketPath = join(folder, "spy.sock");
  const server = app.listen(socketPath);
  await new Promise((resolve) => server.once("listening", resolve));
  try {
    const status = await new Promise<number>((resolve, reject) => {
      const req = request({ socketPath, path: "/", headers: { ...bearer(TOKEN), "x-keep": "1" } }, (res) => {
        res.resume();
        res.on("end", () => resolve(res.statusCode ?? 0));
      });
      req.on("error", reject);
      req.end();
    });
    assert.equal(status, 200);
    assert.equal(seen.length, 1);
    const headers = seen[0]?.headers as Record<string, string>;
    assert.equal("authorization" in headers, false);
    assert.equal(headers["x-keep"], "1");
    assert.equal(seen[0]?.raw.some((v) => v.includes(TOKEN) || v.toLowerCase() === "authorization"), false);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    rmSync(folder, { recursive: true, force: true });
  }
});

test("a refused request never reaches handle; an accepted JSON-RPC request does", async () => {
  let runs = 0;
  const counting: Parameters<typeof buildApp>[0] = async function* (input, ctx, deps) {
    runs += 1;
    const model = scriptedModel([sseBody(["ok"])]);
    yield* handle(input, ctx, { ...deps, fetch: model.fetch, toolFetch: mcpStub({}).fetch });
  };
  const rpc = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "SendStreamingMessage",
    params: {
      message: { role: "ROLE_USER", messageId: "0b1c", contextId: CTX.trace_id, parts: [{ text: "x" }] },
      metadata: {
        "chassis.ctx": JSON.stringify(CTX),
        "chassis.input": JSON.stringify({ text: "x" }),
        "chassis.schema_version": "0",
      },
    },
  });
  await withApp({ token: TOKEN }, counting, async (call) => {
    const json = { "content-type": "application/json", "a2a-version": "1.0" };
    assert.equal((await call("/", json, rpc)).status, 401);
    assert.equal(runs, 0);
    const ok = await call("/", { ...json, ...bearer(TOKEN) }, rpc);
    assert.equal(ok.status, 200);
    assert.equal(runs, 1);
    assert.match(ok.body, /status[^o]{1,6}ok/);
    assert.equal(ok.body.includes(TOKEN), false);
  });
});

test("bearerAllowed: unequal lengths are safe, and a missing header is refused", () => {
  assert.equal(bearerAllowed(undefined, [TOKEN]), false);
  assert.equal(bearerAllowed(`Bearer ${"a".repeat(10_000)}`, [TOKEN]), false);
  assert.equal(bearerAllowed(`Bearer ${TOKEN}`, [OLD, TOKEN]), true);
});

test("parseServeOptions: the flags read the named variables", () => {
  const env = { A: TOKEN, B: OLD, BLANK: "  " };
  assert.deepEqual(parseServeOptions([], env), { token: undefined, previous: undefined });
  assert.deepEqual(parseServeOptions(["--require-token-env", "A"], env), { token: TOKEN, previous: undefined });
  assert.deepEqual(parseServeOptions(["--require-token-env", "A", "--previous-token-env", "B"], env), { token: TOKEN, previous: OLD });
  // an unset or blank previous token is ignored, as in workload_a2a
  assert.equal(parseServeOptions(["--require-token-env", "A", "--previous-token-env", "BLANK"], env).previous, undefined);
  assert.equal(parseServeOptions(["--require-token-env", "A", "--previous-token-env", "NOPE"], env).previous, undefined);
});

test("parseServeOptions: start-up errors name only the variable", () => {
  const env = { A: TOKEN, BLANK: "  ", EMPTY: "" };
  assert.throws(() => parseServeOptions(["--previous-token-env", "A"], env), /needs --require-token-env/);
  for (const name of ["BLANK", "EMPTY", "MISSING"]) {
    assert.throws(() => parseServeOptions(["--require-token-env", name], env), (err: unknown) => {
      assert.ok(err instanceof StartupError);
      assert.match(err.message, new RegExp(`${name} is unset or empty`));
      return true;
    });
  }
  assert.throws(() => parseServeOptions(["--bogus"], env), StartupError);
  assert.throws(() => parseServeOptions(["--require-token-env"], env), StartupError);
});

test("bindHost: a token lifts the loopback guard; without it the guard stands", () => {
  assert.equal(bindHost({ HOST: "0.0.0.0" }, true), "0.0.0.0");
  assert.throws(() => bindHost({ HOST: "0.0.0.0" }, false), /ADR-001/);
  assert.throws(() => bindHost({ HOST: "0.0.0.0" }), /ADR-001/);
});

function startUp(args: string[], env: Record<string, string>): { status: number | null; stderr: string } {
  const main = new URL("../src/main.ts", import.meta.url).pathname;
  const result = spawnSync(process.execPath, ["--import", "tsx", main, ...args], {
    env: { PATH: process.env["PATH"] ?? "", ...env },
    encoding: "utf8",
    timeout: 20_000,
  });
  return { status: result.status, stderr: result.stderr };
}

test("main: a start-up error exits 2 before listening, naming only the variable", () => {
  const secret = "tok-must-not-print";
  const cases: [string[], Record<string, string>, RegExp][] = [
    [["--previous-token-env", "P"], { P: secret }, /needs --require-token-env/],
    [["--require-token-env", "T"], {}, /T is unset or empty/],
    [["--require-token-env", "T"], { T: "   " }, /T is unset or empty/],
    [["--nope"], {}, /nope/],
  ];
  for (const [args, env, pattern] of cases) {
    const { status, stderr } = startUp(args, env);
    assert.equal(status, 2, args.join(" "));
    assert.match(stderr, pattern);
    assert.equal(stderr.includes(secret), false);
  }
  // a non-loopback HOST with no token is still refused
  assert.equal(startUp([], { HOST: "0.0.0.0" }).status, 2);
});
