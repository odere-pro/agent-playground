"""InMemoryTools: a ToolPort over plain Python callables, and the one shared tool,
`glossary_lookup`, defined once here, and the second read-only tool `acronym_expand` (PoC-6).
PoC-5 adds write mode, the allow-list, and
`write_mode_tools()`: the write tool `note_write` and the never-allowed `unlisted_probe`.
`default_tools()` stays read-only.

Arguments are checked against each definition's `parameters` by a small hand-written JSON Schema
check (`check_arguments`): `type`, `properties`, `required`, `additionalProperties: false`, and
`enum`. Other keywords are not checked. `jsonschema` is not a declared chassis dependency, so the
fake does not import it.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from chassis.ports.tool import ToolDefinition, ToolError, ToolResult

ToolFn = Callable[..., Any]
"""Called with the arguments as keywords. May be sync or async. May return a `ToolResult`."""

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _type_ok(value: Any, expected: str) -> bool:
    kinds = _TYPES.get(expected)
    if kinds is None:
        return True  # an unknown type name is not checked
    if isinstance(value, bool) and expected in ("integer", "number"):
        return False
    return isinstance(value, kinds)


def _check(value: Any, schema: Mapping[str, Any], path: str) -> list[str]:
    problems: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        names = [expected] if isinstance(expected, str) else list(expected)
        if not any(_type_ok(value, n) for n in names):
            return [f"{path}: expected {' or '.join(names)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} is not one of {schema['enum']!r}")
    if isinstance(value, dict):
        props: Mapping[str, Any] = schema.get("properties", {})
        problems.extend(
            f"{path}: missing required {key!r}"
            for key in schema.get("required", [])
            if key not in value
        )
        for key, item in value.items():
            if key in props:
                problems.extend(_check(item, props[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                problems.append(f"{path}: unexpected {key!r}")
    return problems


def check_arguments(definition: ToolDefinition, arguments: Mapping[str, Any]) -> None:
    """Raise `ToolError("bad_arguments")` when `arguments` fail the definition's schema."""
    problems = _check(dict(arguments), definition.parameters, "arguments")
    if problems:
        raise ToolError("bad_arguments", f"{definition.name}: {'; '.join(problems)}")


class InMemoryTools:
    """A `ToolPort` over callables. PoC-5 write mode (plan section 2.6):

    - `allowed`: the per-key allow-list in miniature. A tool outside it is not listed, and a call
      to it raises `tool_denied` before it runs. None (the default) allows every tool.
    - A write tool (`read_only=False`) called without `idempotency_key` raises
      `idempotency_key_required` before it runs. With a key, it runs at most once per tool and
      key; a repeat returns the first result, whatever its arguments.
    - `calls` and `idempotency_keys` record every call; `effects` records each write that ran.
    - `fail_next_call(error)` makes the next call raise `error`, for example `tool_unavailable`.
    """

    name = "fake"

    def __init__(
        self,
        tools: Sequence[tuple[ToolDefinition, ToolFn]] = (),
        *,
        allowed: frozenset[str] | None = None,
    ) -> None:
        self._tools: dict[str, tuple[ToolDefinition, ToolFn]] = {}
        for definition, fn in tools:
            self.add(definition, fn)
        self.allowed = allowed
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.idempotency_keys: list[str | None] = []
        self.effects: list[tuple[str, str]] = []
        self._results: dict[tuple[str, str], ToolResult] = {}
        self._fail_next: ToolError | None = None

    def add(self, definition: ToolDefinition, fn: ToolFn) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool {definition.name!r} is defined twice")
        self._tools[definition.name] = (definition, fn)

    def fail_next_call(self, error: ToolError) -> None:
        """The next call raises `error` before anything else is checked."""
        self._fail_next = error

    def _allows(self, name: str) -> bool:
        return self.allowed is None or name in self.allowed

    def list_tools(self) -> Sequence[ToolDefinition]:
        return [d for d, _ in self._tools.values() if self._allows(d.name)]

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str | None = None
    ) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        self.idempotency_keys.append(idempotency_key)
        if self._fail_next is not None:
            error, self._fail_next = self._fail_next, None
            raise error
        entry = self._tools.get(name)
        if entry is None:
            raise ToolError("unknown_tool", f"no tool named {name!r}")
        if not self._allows(name):
            raise ToolError("tool_denied", f"{name} is not allowed for this credential")
        definition, fn = entry
        write = not definition.read_only
        if write and idempotency_key is None:
            raise ToolError("idempotency_key_required", f"{name} is a write tool")
        if write and idempotency_key is not None:
            # Plan section 2.6: one effect per key; a reused key returns the first result,
            # whatever its arguments (the contract suite pins it).
            first = self._results.get((name, idempotency_key))
            if first is not None:
                return first
        check_arguments(definition, arguments)
        try:
            inspect.signature(fn).bind(**arguments)
        except TypeError as exc:
            raise ToolError("bad_arguments", f"{name}: {exc}") from exc
        out = fn(**arguments)
        if inspect.isawaitable(out):
            out = await out
        result = out if isinstance(out, ToolResult) else ToolResult(content=out)
        if write and idempotency_key is not None:
            self._results[(name, idempotency_key)] = result
            self.effects.append((name, idempotency_key))
        return result


GLOSSARY: dict[str, str] = {
    "SLM": "A small language model: a model small enough to run cheaply on one GPU or a CPU.",
    "A2A": "Agent-to-Agent, the protocol the chassis uses to send a task to a workload.",
    "MCP": "Model Context Protocol, the protocol the chassis uses to serve tools.",
    "chassis": "The service around a workload: it holds keys, calls models and tools, and traces.",
    "sidecar": "A lane where the workload runs in its own container next to the chassis.",
    "LiteLLM": "The model router the chassis calls; it holds routes, keys, and budgets.",
    "workload": "The agent code that runs behind the chassis and answers one request.",
    "lane": "How the chassis reaches a workload: inprocess, sidecar, or remote.",
    "port": "A typed interface the chassis uses for one outside dependency.",
    "fake": "An in-memory stand-in for a port, used in tests and the fake profile.",
    "traceparent": "The W3C header that carries the trace id from one call to the next.",
}

GLOSSARY_LOOKUP = ToolDefinition(
    name="glossary_lookup",
    description="Look up a platform term. Returns its short definition, or null if unknown.",
    parameters={
        "type": "object",
        "properties": {"term": {"type": "string", "description": "The term to look up."}},
        "required": ["term"],
        "additionalProperties": False,
    },
    read_only=True,
)


def glossary_lookup(term: str) -> dict[str, str | None]:
    """Exact match first, then a case-insensitive match. An unknown term is not an error."""
    definition = GLOSSARY.get(term)
    if definition is None:
        folded = {k.casefold(): v for k, v in GLOSSARY.items()}
        definition = folded.get(term.casefold())
    return {"term": term, "definition": definition}


ACRONYMS: dict[str, str] = {
    "RAG": "retrieval-augmented generation",
    "SLM": "small language model",
    "LLM": "large language model",
    "MCP": "Model Context Protocol",
    "A2A": "Agent-to-Agent",
    "TTFT": "time to first token",
}
"""suggested: the acronyms `acronym_expand` knows. The same data as `packages/fake-mcp-server`."""

ACRONYM_EXPAND = ToolDefinition(
    name="acronym_expand",
    description="Expand an acronym. Returns its expansion, or null if unknown.",
    parameters={
        "type": "object",
        "properties": {"acronym": {"type": "string", "description": "The acronym to expand."}},
        "required": ["acronym"],
        "additionalProperties": False,
    },
    read_only=True,
)


def acronym_expand(acronym: str) -> dict[str, str | None]:
    """Exact match first, then a case-insensitive match. An unknown acronym is not an error."""
    expansion = ACRONYMS.get(acronym)
    if expansion is None:
        folded = {k.casefold(): v for k, v in ACRONYMS.items()}
        expansion = folded.get(acronym.casefold())
    return {"acronym": acronym, "expansion": expansion}


def default_tools() -> InMemoryTools:
    """The tools every workload gets in the fake profile: `glossary_lookup`, `acronym_expand`."""
    return InMemoryTools([(GLOSSARY_LOOKUP, glossary_lookup), (ACRONYM_EXPAND, acronym_expand)])


NOTE_WRITE = ToolDefinition(
    name="note_write",
    description="Store a short note. A write tool: each call needs an idempotency key.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The note to store."}},
        "required": ["text"],
        "additionalProperties": False,
    },
    read_only=False,
)
"""suggested: the write tool. The same name and schema as `packages/fake-mcp-server`."""


class NoteStore:
    """The `note_write` tool: stores each note and returns its 1-based id, so each effect has a
    result of its own. The dedup by key is the port's (`InMemoryTools`), not this function's.
    """

    def __init__(self) -> None:
        self.notes: list[str] = []

    def __call__(self, text: str) -> dict[str, Any]:
        self.notes.append(text)
        return {"note_id": len(self.notes), "text": text}


UNLISTED_PROBE = ToolDefinition(
    name="unlisted_probe",
    description="Return a fixed marker. Never on an allow-list: seeing the marker is a failure.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    read_only=True,
)
"""suggested: the H08 target. It exists behind the port and is outside every allow-list."""

UNLISTED_MARKER = "unlisted-probe-reached"
"""suggested: what `unlisted_probe` returns. A test that sees it found an allow-list failure."""

WRITE_MODE_ALLOWED = frozenset({GLOSSARY_LOOKUP.name, NOTE_WRITE.name})
"""What a service key may call in the write-mode set: everything but `unlisted_probe`."""


class WriteModeTools(InMemoryTools):
    """`InMemoryTools` with `glossary_lookup`, `note_write`, and `unlisted_probe`; `notes` and
    `note_store` reach the write tool's store."""

    def __init__(self, allowed: frozenset[str] | None) -> None:
        self.note_store = NoteStore()
        super().__init__(
            [
                (GLOSSARY_LOOKUP, glossary_lookup),
                (NOTE_WRITE, self.note_store),
                (UNLISTED_PROBE, lambda: UNLISTED_MARKER),
            ],
            allowed=allowed,
        )

    @property
    def notes(self) -> list[str]:
        return self.note_store.notes


def write_mode_tools(allowed: frozenset[str] | None = WRITE_MODE_ALLOWED) -> WriteModeTools:
    """The set a gateway key sees in PoC-5: `glossary_lookup` and `note_write` listed,
    `unlisted_probe` behind the port but outside `allowed`. Mirrors `packages/fake-mcp-server`.
    """
    return WriteModeTools(allowed)
