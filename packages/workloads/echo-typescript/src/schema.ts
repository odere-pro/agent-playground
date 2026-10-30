// A small validator for the vendored events.v0.json (a copy of packages/chassis/schemas/). It
// reads the schema and understands only the keywords that file uses: the `type` discriminator,
// `type`, `const`, `enum`, `anyOf`, `required`, `properties`, `additionalProperties`. A keyword
// it does not know makes it throw at load, so a schema change cannot pass unchecked. A valid event
// gets the schema's defaults filled, as the Python template's `validate_event` does
// (packages/workload-a2a/src/workload_a2a/server.py), so both put the same JSON on the wire.

import schema from "../schemas/events.v0.json" with { type: "json" };

import type { ChassisEvent } from "./handle.js";

export type Json = Record<string, unknown>;
type Node = Record<string, unknown>;
const KNOWN = new Set(["type", "const", "enum", "anyOf", "required", "properties", "additionalProperties", "title", "default", "description"]);
const root = schema as unknown as { $defs: Record<string, Node>; discriminator: { mapping: Record<string, string> } };
const props = (def: Node): Record<string, Node> => (def["properties"] ?? {}) as Record<string, Node>;

/** Versions in number order, so "10" comes after "9" (a string sort puts it first). */
export const sortVersions = (versions: Iterable<string>): string[] => [...new Set(versions)].sort((a, b) => Number(a) - Number(b));

/** The versions the server accepts: the `schema_version` consts in the vendored schema, sorted. */
export const SUPPORTED_SCHEMA_VERSIONS: readonly string[] = sortVersions(
  Object.values(root.$defs).map((def) => String(props(def)["schema_version"]?.["const"])),
);
/** The version this workload writes: the newest supported one. */
export const SCHEMA_VERSION: string = SUPPORTED_SCHEMA_VERSIONS.at(-1) ?? "0";

/** Defaults the chassis models build with a factory, which the JSON Schema does not carry. */
const FACTORY_DEFAULTS: Record<string, Json> = { tool_call: { arguments: {} } };

/** The defaults for one event type: each property's schema `default`, then the factory ones. */
function defaultsOf(type: string, def: Node): Json {
  const fromSchema = Object.entries(props(def)).filter(([, spec]) => "default" in spec).map(([name, spec]) => [name, spec["default"]]);
  return { ...Object.fromEntries(fromSchema), ...FACTORY_DEFAULTS[type] };
}

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
  for (const name of (node["required"] ?? []) as string[]) if (!(name in obj)) throw new Error(`${path}.${name}: required`);
  const fields = props(node);
  for (const [name, v] of Object.entries(obj)) {
    const sub = fields[name];
    if (sub) check(sub, v, `${path}.${name}`);
    else if (node["additionalProperties"] === false) throw new Error(`${path}.${name}: unknown field`);
  }
}

/** The event with the schema's missing defaults filled when it matches events.v0.json; otherwise
 * throws with the path. The input is not changed. */
export function validateEvent(raw: unknown): ChassisEvent {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw new Error("event: must be an object");
  const type = (raw as Record<string, unknown>)["type"];
  const ref = typeof type === "string" ? root.discriminator.mapping[type] : undefined;
  const def = ref ? root.$defs[ref.replace("#/$defs/", "")] : undefined;
  if (!def) throw new Error(`event.type: ${JSON.stringify(type)} is not an event type`);
  check(def, raw, "event");
  return withDefaults(raw as Json, defaultsOf(type as string, def));
}

/** `event` in its own key order with each missing default after its keys; a trailing
 * `schema_version` stays last (the fixture's shape). Defaults are cloned, never shared. */
function withDefaults(event: Json, defaults: Json): ChassisEvent {
  const missing = structuredClone(Object.fromEntries(Object.entries(defaults).filter(([name]) => !(name in event))));
  const keys = Object.keys(event);
  if (keys.at(-1) !== "schema_version") return { ...event, ...missing } as ChassisEvent;
  const { schema_version: version, ...rest } = event;
  return { ...rest, ...missing, schema_version: version } as ChassisEvent;
}
