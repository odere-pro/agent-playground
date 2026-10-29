"""PoC-8 exit criterion 8: "No engine-specific code outside the workloads' event mappings."
(docs/planning/poc/008-PoC-8-build-cross-cutting.md)

A static scan of the chassis (`packages/chassis/src`) and the workload template server
(`packages/workload-a2a/src`: it ships with every Python workload, so it must stay engine-neutral
too). It extends PoC-7's `test_the_chassis_has_no_engine_specific_branch`:

1. Code: no identifier, attribute, import, or string literal (docstrings and comments are not
   code) names an engine, framework, or instrumentor. No allow-list.
2. Imports: no module imports a framework package, by its import root.
3. Text: every mention anywhere in the file, docstrings and comments included, is on the
   allow-list below, each entry with its reason. A new mention fails until someone justifies it.
   An entry that matches nothing fails too, so the list cannot go stale.

The dynamic half is the rest of this folder: one chassis app, one port set, every cell.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCANNED = (
    ROOT / "packages/chassis/src/chassis",
    ROOT / "packages/workload-a2a/src/workload_a2a",
)
ENGINE_NAMES = re.compile(
    r"echo[-_ ]?(?:python|pydanticai|langgraph|openai[-_ ]agents|smolagents|claude[-_ ]agent|"
    r"typescript)|langgraph|langchain|pydantic[-_ ]?ai|openai[-_ ]agents|agents[-_ ]sdk|"
    r"smolagents|claude[-_ ]agent|claude_agent_sdk|kagent|crewai|google[-_ .]adk|"
    r"openinference",
    re.IGNORECASE,
)
FRAMEWORK_IMPORTS = frozenset(
    {
        "agents",  # the OpenAI Agents SDK's import name
        "claude_agent_sdk",
        "crewai",
        "google.adk",
        "kagent",
        "langchain",
        "langchain_core",
        "langchain_openai",
        "langgraph",
        "openinference",
        "pydantic_ai",
        "smolagents",
    }
)
ALLOWED: dict[tuple[str, str], str] = {
    (
        "packages/chassis/src/chassis/server/model_proxy_messages.py",
        "claude agent",
    ): "module docstring: names the client of `POST /v1/messages` (contract v5, part A); the "
    "route is the Anthropic wire shape, not an engine branch",
    (
        "packages/chassis/src/chassis/adapters/anthropic_compat/model_wire.py",
        "claude agent",
    ): "module docstring: says why the body is read by hand (what that client sends)",
    (
        "packages/chassis/src/chassis/adapters/otel/__init__.py",
        "openinference",
    ): "attribute docstring: the baggage keys follow the OpenInference naming standard, which "
    "Langfuse reads; a span convention, not an engine",
    (
        "packages/chassis/src/chassis/adapters/litellm/client.py",
        "echo_python",
    ): "comment: points to the workload that caps its own call time today (013 CH-2)",
    (
        "packages/chassis/src/chassis/adapters/a2a/server.py",
        "pydanticai",
    ): "attribute docstring: an example of a handle that holds an anyio cancel scope",
    (
        "packages/chassis/src/chassis/adapters/a2a/server.py",
        "langgraph",
    ): "attribute docstring: the same example",
    (
        "packages/chassis/src/chassis/adapters/a2a/plain.py",
        "kagent",
    ): "docstring and comments: the plain-A2A mode (ADR-006 item 4) records which third-party "
    "wire shapes it reads; it reads any plain A2A agent the same way",
    (
        "packages/workload-a2a/src/workload_a2a/telemetry.py",
        "openinference",
    ): "module docstring: each engine turns its own OpenInference instrumentor on through "
    "`register_instrumentor`; the template names none",
    (
        "packages/workload-a2a/src/workload_a2a/server.py",
        "pydanticai",
    ): "attribute docstring: the mirror of the chassis's `a2a/server.py` example",
    (
        "packages/workload-a2a/src/workload_a2a/server.py",
        "langgraph",
    ): "attribute docstring: the same example",
    (
        "packages/workload-a2a/src/workload_a2a/cli.py",
        "echo_python",
    ): "the `--handle` help text: an example handle path for the operator",
}


def _files() -> list[Path]:
    return sorted(p for base in SCANNED for p in base.rglob("*.py"))


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _code_tokens(tree: ast.AST) -> list[str]:
    """Every identifier, import, and string in a module except documentation: bare string
    statements (docstrings, attribute docs) and the `help=` text of a command-line option.
    Comments are not in the tree."""
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    docstrings |= {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "help"
        and isinstance(node.value, ast.Constant)
    }
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
        elif isinstance(node, ast.alias):
            out.append(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            out.append(node.value)
    return out


def _imports(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def _framework(module: str) -> bool:
    return any(module == root or module.startswith(f"{root}.") for root in FRAMEWORK_IMPORTS)


def _term(match: str) -> str:
    """A hit, folded to the allow-list's spelling: lower case, `-` and `_` as a space, except
    for the module names that are spelled as written."""
    lowered = match.lower()
    return lowered if "_" in lowered and lowered.startswith("echo") else lowered.replace("-", " ")


def test_the_scan_sees_the_chassis() -> None:
    """The control: the scan covers the chassis modules that run every stage."""
    names = {_rel(p) for p in _files()}
    for module in ("server/pipeline.py", "server/guard.py", "server/evaluator_gate.py"):
        assert f"packages/chassis/src/chassis/{module}" in names
    assert ENGINE_NAMES.search("from smolagents import CodeAgent")


def test_no_engine_name_in_chassis_code() -> None:
    """Criterion 8 (code): no engine, framework, or instrumentor name in any code token of the
    chassis or the template server. No allow-list."""
    offenders = [
        f"{_rel(path)}: {token}"
        for path in _files()
        for token in _code_tokens(ast.parse(path.read_text()))
        if ENGINE_NAMES.search(token)
    ]
    assert offenders == []


def test_no_framework_import_in_the_chassis() -> None:
    """Criterion 8 (imports): no module imports an agent framework or its instrumentor."""
    offenders = [
        f"{_rel(path)}: import {module}"
        for path in _files()
        for module in _imports(ast.parse(path.read_text()))
        if _framework(module)
    ]
    assert offenders == []


def test_every_mention_in_text_is_on_the_allow_list() -> None:
    """Criterion 8 (text): a mention in a docstring or comment is allowed only with a reason
    in `ALLOWED`; every entry still matches a mention."""
    found = {
        (_rel(path), _term(m.group(0)))
        for path in _files()
        for m in ENGINE_NAMES.finditer(path.read_text())
    }
    unlisted = sorted(found - set(ALLOWED))
    stale = sorted(set(ALLOWED) - found)
    assert unlisted == [], "justify each in ALLOWED, or move the code to the workload"
    assert stale == [], "remove the entries that match nothing"
