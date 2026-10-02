// The bind guard, as in workload_a2a's `build_server`: the sidecar serves localhost only
// (ADR-001), so a HOST that is not loopback is refused unless ALLOW_ANY_HOST=1. Pure: no socket.

import { BlockList, isIPv4, isIPv6 } from "node:net";

const LOOPBACK = new BlockList();
LOOPBACK.addSubnet("127.0.0.0", 8, "ipv4");
LOOPBACK.addAddress("::1", "ipv6");

/** `localhost` or a loopback IP (`127.0.0.0/8`, `::1`). */
export function isLoopback(host: string): boolean {
  if (host === "localhost") return true;
  const ip = host.replace(/^\[(.*)\]$/, "$1");
  if (isIPv4(ip)) return LOOPBACK.check(ip, "ipv4");
  if (isIPv6(ip)) return LOOPBACK.check(ip, "ipv6");
  return false;
}

/** The host to bind: HOST (default 127.0.0.1), refused when not loopback unless ALLOW_ANY_HOST=1. */
export function bindHost(env: Readonly<Record<string, string | undefined>>): string {
  const host = env["HOST"] ?? "127.0.0.1";
  if (env["ALLOW_ANY_HOST"] !== "1" && !isLoopback(host)) {
    throw new Error(
      `host ${JSON.stringify(host)} is not loopback; the sidecar serves localhost only (ADR-001). ` +
        "Set ALLOW_ANY_HOST=1 to bind it anyway",
    );
  }
  return host;
}
