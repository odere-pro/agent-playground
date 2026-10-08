import assert from "node:assert/strict";
import { test } from "node:test";

import { bindHost, isLoopback } from "../src/host.js";

test("loopback hosts are loopback", () => {
  for (const host of ["127.0.0.1", "127.8.9.10", "localhost", "::1", "[::1]", "0:0:0:0:0:0:0:1"]) {
    assert.equal(isLoopback(host), true, host);
  }
});

test("other hosts are not loopback", () => {
  for (const host of ["0.0.0.0", "::", "10.0.0.5", "192.168.1.2", "example.com", "", "128.0.0.1"]) {
    assert.equal(isLoopback(host), false, host);
  }
});

test("HOST defaults to 127.0.0.1", () => {
  assert.equal(bindHost({}), "127.0.0.1");
});

test("a loopback HOST is kept", () => {
  assert.equal(bindHost({ HOST: "::1" }), "::1");
});

test("a non-loopback HOST is refused, naming ADR-001", () => {
  for (const env of [{ HOST: "0.0.0.0" }, { HOST: "0.0.0.0", ALLOW_ANY_HOST: "true" }]) {
    assert.throws(() => bindHost(env), (err: unknown) => {
      assert.ok(err instanceof Error);
      assert.match(err.message, /"0\.0\.0\.0" is not loopback/);
      assert.match(err.message, /ADR-001/);
      assert.match(err.message, /ALLOW_ANY_HOST=1/);
      return true;
    });
  }
});

test("ALLOW_ANY_HOST=1 lets a non-loopback HOST through", () => {
  assert.equal(bindHost({ HOST: "0.0.0.0", ALLOW_ANY_HOST: "1" }), "0.0.0.0");
});
