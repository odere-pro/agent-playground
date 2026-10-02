// A small validator for the vendored events.v0.json (a copy of packages/chassis/schemas/). It
// reads the schema and understands only the keywords that file uses: the `type` discriminator,
// `type`, `const`, `enum`, `anyOf`, `required`, `properties`, `additionalProperties`. A keyword
// it does not know makes it throw at load, so a schema change cannot pass unchecked.

import schema from "../schemas/events.v0.json" with { type: "json" };

import type { ChassisEvent } from "./handle.js";

type Node = Record<string, unknown>;
const KNOWN = new Set(["type", "const", "enum", "anyOf", "required", "properties", "additionalProperties", "title", "default", "description"]);
const root = schema as unknown as { $defs: Record<string, Node>; discriminator: { mapping: Record<string, string> } };

function check(node: Node, value: unknown, path: string): void {
  for (const key of Object.keys(node)) if (!KNOWN.has(key)) throw new Error(`schema keyword ${key} at ${path} is not supported`);
  if ("anyOf" in node) {
    const options = node["anyOf"] as Node[];
    if (!options.some((o) => { try { check(o, value, path); return true; } catch { return false; } }))
      throw new Error(`${path}: ${JSON.stringify(value)} matches none of anyOf`);
    return;
  }
  if ("const" in node && value !== node["const"]) throw new Error(`${path}: must be ${JSON.stringify(node["const"])}`);
  if ("enum" in node && !(node["enum"] as unknown[]).includes(value)) throw new Error(`${path}: must be one of ${JSON.stringify(node["enum"])}`);
  const type = node["type"];
  const ok =
    type === undefined ||
    (type === "string" && typeof value === "string") ||
    (type === "boolean" && typeof value === "boolean") ||
    (type === "number" && typeof value === "number" && Number.isFinite(value)) ||
    (type === "integer" && Number.isInteger(value)) ||
    (type === "null" && value === null) ||
    (type === "object" && typeof value === "object" && value !== null && !Array.isArray(value));
  if (!ok) throw new Error(`${path}: must be ${String(type)}`);
  if (type !== "object") return;
  const obj = value as Record<string, unknown>;
  const props = (node["properties"] ?? {}) as Record<string, Node>;
  for (const name of (node["required"] ?? []) as string[]) if (!(name in obj)) throw new Error(`${path}.${name}: required`);
  for (const [name, v] of Object.entries(obj)) {
    const sub = props[name];
    if (sub) check(sub, v, `${path}.${name}`);
    else if (node["additionalProperties"] === false) throw new Error(`${path}.${name}: unknown field`);
  }
}

/** The event, unchanged, when it matches events.v0.json; otherwise throws with the path. */
export function validateEvent(raw: unknown): ChassisEvent {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw new Error("event: must be an object");
  const type = (raw as Record<string, unknown>)["type"];
  const ref = typeof type === "string" ? root.discriminator.mapping[type] : undefined;
  const def = ref ? root.$defs[ref.replace("#/$defs/", "")] : undefined;
  if (!def) throw new Error(`event.type: ${JSON.stringify(type)} is not an event type`);
  check(def, raw, "event");
  return raw as ChassisEvent;
}
