// The inbound bearer check for the remote lane, as workload_a2a's `auth.py` and `cli.py`:
// `--require-token-env NAME` and `--previous-token-env NAME`. Every request, the agent card
// included, needs `Authorization: Bearer <token>`. A missing or wrong token gets one fixed 401 and
// the app is not called. During a rotation a second, previous token is accepted too. After a good
// check the `authorization` header is removed before the A2A app sees it, because SDK logs may
// carry request headers. No token is ever logged or put in an error: only the variable's name.

import { createHash, timingSafeEqual } from "node:crypto";
import type { NextFunction, Request, RequestHandler, Response } from "express";
import { parseArgs } from "node:util";

export const UNAUTHORIZED_BODY = '{"error":"unauthorized"}';

/** A bad command line or token variable; `main` prints the message and exits 2. */
export class StartupError extends Error {}

export interface ServeOptions {
  /** The token every request must carry; `undefined` when no check is asked for. */
  token: string | undefined;
  /** The extra token accepted during a rotation, or `undefined`. */
  previous: string | undefined;
}

type Env = Readonly<Record<string, string | undefined>>;

/** The token in `$name`. A missing or blank variable throws, naming the variable, never a value. */
export function readToken(name: string, env: Env): string {
  const value = env[name] ?? "";
  if (!value.trim()) throw new StartupError(`--require-token-env: environment variable ${name} is unset or empty`);
  return value;
}

/** The previous token in `$name`, or `undefined` when unset or blank (not an error: the same
 * manifest runs before, during, and after a rotation). */
export function readPreviousToken(name: string, env: Env): string | undefined {
  const value = env[name] ?? "";
  return value.trim() ? value : undefined;
}

/** Parse the flags of the entry point and read the token variables. */
export function parseServeOptions(argv: readonly string[], env: Env): ServeOptions {
  let values: { "require-token-env"?: string | undefined; "previous-token-env"?: string | undefined };
  try {
    ({ values } = parseArgs({
      args: [...argv],
      options: { "require-token-env": { type: "string" }, "previous-token-env": { type: "string" } },
      strict: true,
      allowPositionals: false,
    }));
  } catch (exc) {
    throw new StartupError(exc instanceof Error ? exc.message : String(exc));
  }
  const requireName = values["require-token-env"];
  const previousName = values["previous-token-env"];
  if (previousName && !requireName) throw new StartupError("--previous-token-env needs --require-token-env");
  return {
    token: requireName ? readToken(requireName, env) : undefined,
    previous: previousName ? readPreviousToken(previousName, env) : undefined,
  };
}

const digest = (value: string): Buffer => createHash("sha256").update(value).digest();

/** True when `header` is `Bearer <t>` and `<t>` equals any accepted token. Both sides are hashed
 * first, so `timingSafeEqual` always sees equal lengths; every token is compared, no early exit. */
export function bearerAllowed(header: string | undefined, accepted: readonly string[]): boolean {
  let presented = "";
  if (header !== undefined) {
    const cut = header.indexOf(" ");
    const scheme = cut < 0 ? header : header.slice(0, cut);
    if (scheme.toLowerCase() === "bearer") presented = cut < 0 ? "" : header.slice(cut + 1);
  }
  const seen = digest(presented);
  let ok = false;
  for (const token of accepted) ok = timingSafeEqual(seen, digest(token)) || ok;
  return ok;
}

/** Express middleware: refuse every request without an accepted bearer, strip the header on success. */
export function bearerAuth(token: string, previous?: string): RequestHandler {
  if (!token) throw new StartupError("token must not be empty");
  const accepted = previous ? [token, previous] : [token];
  return (req: Request, res: Response, next: NextFunction): void => {
    if (!bearerAllowed(req.headers.authorization, accepted)) {
      res.status(401).set({ "content-type": "application/json", "www-authenticate": "Bearer" }).send(UNAUTHORIZED_BODY);
      return;
    }
    delete req.headers.authorization;
    const raw = req.rawHeaders;
    for (let i = raw.length - 2; i >= 0; i -= 2) if (raw[i]?.toLowerCase() === "authorization") raw.splice(i, 2);
    next();
  };
}
