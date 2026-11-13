"""055 CH-6 on kind: every remote's token Secret, `remote-<name>-token`, holds two keys, one per
direction, in the PoC-5 and PoC-6 manifests and seed scripts.

- `chassis-to-workload`: the chassis sends it (`REMOTE_TOKEN`, `spec.engine.auth.token_env`);
  the workload requires it (`CHASSIS_A2A_TOKEN`, `--require-token-env`).
- `workload-to-chassis`: the workload sends it (`CHASSIS_API_TOKEN`); the chassis's remote
  listener accepts only it (`REMOTE_PROXY_TOKEN`, `spec.engine.auth.proxy_token_env`).

The seed makes both values with two separate `new_secret` calls, through a pipe, never a file.
The admission fixtures are rule inputs, not deployments, and are left out.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from poc_harness.kind_controls import (
    REMOTE_TOKEN_KEYS,
    remote_token_sources,
    secret_keys,
    secret_sources,
)

ROOT = Path(__file__).resolve().parents[3]
KIND_DIRS = (ROOT / "deploy/kind/poc05", ROOT / "deploy/kind/poc06")
SEEDS = (ROOT / "deploy/kind/poc05/platform/seed.sh", ROOT / "deploy/kind/poc06/seed.sh")
TOKEN_SECRET = re.compile(r"^remote-[a-z0-9-]+-token$")
TO_WORKLOAD, TO_CHASSIS = "chassis-to-workload", "workload-to-chassis"
KEY_OF = {
    "REMOTE_TOKEN": TO_WORKLOAD,
    "REMOTE_PROXY_TOKEN": TO_CHASSIS,
    "CHASSIS_A2A_TOKEN": TO_WORKLOAD,
    "CHASSIS_API_TOKEN": TO_CHASSIS,
}
"""Each variable that may read the token Secret, and the one key it must read."""
Doc = dict[str, Any]


def _manifests() -> list[tuple[str, Doc]]:
    out: list[tuple[str, Doc]] = []
    for folder in KIND_DIRS:
        for path in sorted(folder.rglob("*.yaml")):
            if "admission" in path.parts:
                continue
            where = str(path.relative_to(ROOT))
            docs = yaml.safe_load_all(path.read_text())
            out.extend((where, doc) for doc in docs if isinstance(doc, dict))
    return out


def _containers(node: Any) -> Iterator[Doc]:
    if isinstance(node, dict):
        for key in ("containers", "initContainers"):
            for c in node.get(key) or []:
                if isinstance(c, dict):
                    yield c
        for value in node.values():
            yield from _containers(value)
    elif isinstance(node, list):
        for item in node:
            yield from _containers(item)


def _token_env(container: Doc) -> dict[str, str]:
    """{variable: key} for every env entry that reads a remote token Secret."""
    out: dict[str, str] = {}
    for env in container.get("env") or []:
        ref = (env.get("valueFrom") or {}).get("secretKeyRef") or {}
        if TOKEN_SECRET.fullmatch(str(ref.get("name", ""))):
            out[env["name"]] = ref["key"]
    return out


TOKEN_CONTAINERS = [
    (f"{where}:{c['name']}", c)
    for where, doc in _manifests()
    for c in _containers(doc)
    if _token_env(c)
]


def test_the_scan_finds_the_remote_chassis_and_workloads() -> None:
    """Control: an empty scan proves nothing. PoC-5 has one remote, PoC-6 four chassis."""
    names = {where for where, _ in TOKEN_CONTAINERS}
    assert any("poc05/agents/chassis-echo-remote" in n for n in names), names
    assert any("poc05/remote/remote-echo" in n for n in names), names
    assert sum("poc06" in n and n.endswith(":chassis") for n in names) >= 4, names


@pytest.mark.parametrize(("where", "container"), TOKEN_CONTAINERS, ids=lambda x: str(x)[:60])
def test_each_direction_reads_its_own_key(where: str, container: Doc) -> None:
    """A chassis reads both keys, each into its own variable; a workload reads the key it
    requires and the key it sends. No container reads one key into the other's variable."""
    refs = _token_env(container)
    assert set(refs) <= set(KEY_OF), (where, refs)
    assert {name: KEY_OF[name] for name in refs} == refs, (where, refs)
    if "REMOTE_TOKEN" in refs or "REMOTE_PROXY_TOKEN" in refs:
        assert set(refs) == {"REMOTE_TOKEN", "REMOTE_PROXY_TOKEN"}, (where, refs)
        return
    args = [str(a) for a in container.get("args") or []]
    if "--require-token-env" in args:
        assert args[args.index("--require-token-env") + 1] == "CHASSIS_A2A_TOKEN", where
        assert set(refs) == {"CHASSIS_A2A_TOKEN", "CHASSIS_API_TOKEN"}, (where, refs)
    assert refs.get("CHASSIS_API_TOKEN", TO_CHASSIS) == TO_CHASSIS, where


def test_every_remote_chassis_config_names_one_key_per_direction() -> None:
    """Every chassis config with a remote bearer names the listener's own key too."""
    found = []
    want = {
        "scheme": "bearer",
        "token_env": "REMOTE_TOKEN",
        "proxy_token_env": "REMOTE_PROXY_TOKEN",
    }
    for where, doc in _manifests():
        engine = (doc.get("spec") or {}).get("engine")
        auth = engine.get("auth") if isinstance(engine, dict) else None
        if auth:
            found.append(where)
            assert auth == want, where
    assert len(found) >= 5, found


REMOTE_WORKLOADS = {
    "remote-echo": ROOT / "deploy/kind/poc05/remote/remote-echo.yaml",
    "remote-smolagents": ROOT / "deploy/kind/poc06/remote/remote-smolagents.yaml",
    "remote-claude-agent": ROOT / "deploy/kind/poc06/remote/remote-claude-agent.yaml",
    "remote-typescript": ROOT / "deploy/kind/poc06/remote/remote-typescript.yaml",
}


@pytest.mark.parametrize("name", sorted(REMOTE_WORKLOADS))
def test_the_live_secret_check_expects_what_the_manifest_mounts(name: str) -> None:
    """The kind criterion-4 tests (`test_poc05_kind_remote_controls.py`,
    `test_poc06b_kind_remote_controls.py`) expect `remote_token_sources` and
    `REMOTE_TOKEN_KEYS`. This pins the shipped pod specs to the same expectation offline: two
    Secret sources, both from the remote's own Secret, each reading its own direction's key."""
    docs = yaml.safe_load_all(REMOTE_WORKLOADS[name].read_text())
    spec = next(
        d["spec"]["podTemplate"]["spec"] for d in docs if "podTemplate" in d.get("spec", {})
    )
    assert secret_sources(spec) == remote_token_sources("workload", f"{name}-token")
    assert secret_keys(spec) == REMOTE_TOKEN_KEYS


KEY_NAMES_ONLY = "go-template={{range $k, $_ := .data}}{{$k}} {{end}}"
"""Prints the Secret's key names, never a value."""


@pytest.mark.parametrize("seed", SEEDS, ids=lambda p: p.parent.name)
def test_the_seed_refuses_an_old_one_key_secret_and_can_rotate_it(seed: Path) -> None:
    """An existing Secret is kept only if it holds both keys, checked by name only. The old
    one-key Secret stops the seed with a message that names the rotate command, and
    `seed.sh rotate remote-<name>-token` drops and remakes it."""
    text = seed.read_text()
    assert KEY_NAMES_ONLY in text, seed
    assert "{{$_}}" not in text and "{{.}}" not in text, "a template must not print a value"
    assert re.search(r"token_keys_ok\(\) \{", text), seed
    assert re.search(r'token_keys_ok "\$AGENTS" "remote-\$name-token"', text), seed
    assert re.search(r"die \"[^\"]*one-key[^\"]*rotate remote-\$name-token", text), seed
    rotate = re.search(r"(?ms)^    remote-\*-token\)\n(.*?);;", text)
    assert rotate, f"{seed}: no rotate case for remote-*-token"
    assert 'drop_secret "$AGENTS" "$name"' in rotate.group(1), seed
    assert "seed_keys" in rotate.group(1), seed


@pytest.mark.parametrize("seed", SEEDS, ids=lambda p: p.parent.name)
def test_the_seed_makes_two_distinct_random_keys_through_a_pipe(seed: Path) -> None:
    """Both keys come from separate `new_secret` calls, are compared before use, and go into
    the Secret together through `put_env_secret` (stdin); no single `token` key is made."""
    text = seed.read_text()
    assert not re.search(r'put_secret "\$\w+" "remote-\$name-token" token', text), seed
    assert re.search(r"to_workload=\$\(new_secret\)", text), seed
    assert re.search(r"to_chassis=\$\(new_secret\)", text), seed
    assert re.search(r'\[\[ \$to_workload != "\$to_chassis" \]\]', text), seed
    puts = re.findall(r'put_env_secret "\$(\w+)" "remote-\$name-token"', text)
    assert puts and set(puts) >= {"AGENTS"}, (seed, puts)
    assert f"{TO_WORKLOAD}=" in text and f"{TO_CHASSIS}=" in text, seed
