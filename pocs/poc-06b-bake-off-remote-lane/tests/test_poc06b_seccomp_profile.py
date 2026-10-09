"""The PoC-6 gVisor seccomp profile derivation (deploy/kind/poc06/seccomp/derive_profile.py).

Offline: the input is a small dict shaped like `crictl inspect` output for a RuntimeDefault
container. The derived profile must differ from the input in exactly the `clone3` rule, and every
input that is not exactly the expected shape must fail closed.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy/kind/poc06/seccomp/derive_profile.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("derive_profile", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


derive_profile = _load()


def inspect_doc(syscalls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A fixture shaped like containerd's output (a short syscall list, not the real one)."""
    rules = (
        syscalls
        if syscalls is not None
        else [
            {"names": ["accept", "accept4", "read", "write"], "action": "SCMP_ACT_ALLOW"},
            {"names": ["clone3"], "action": "SCMP_ACT_ERRNO", "errnoRet": 38},
            {
                "names": ["clone"],
                "action": "SCMP_ACT_ALLOW",
                "args": [{"index": 0, "value": 2114060288, "op": "SCMP_CMP_MASKED_EQ"}],
            },
            {"names": ["bpf", "mount"], "action": "SCMP_ACT_ERRNO", "errnoRet": 1},
        ]
    )
    return {
        "status": {"id": "abc"},
        "info": {
            "runtimeSpec": {
                "ociVersion": "1.1.0",
                "linux": {
                    "seccomp": {
                        "defaultAction": "SCMP_ACT_ERRNO",
                        "defaultErrnoRet": 1,
                        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
                        "syscalls": rules,
                    }
                },
            }
        },
    }


def test_happy_path_changes_exactly_the_clone3_rule() -> None:
    doc = inspect_doc()
    source = doc["info"]["runtimeSpec"]["linux"]["seccomp"]
    before = copy.deepcopy(doc)
    out = derive_profile.derive(doc)
    assert doc == before, "the input must not be mutated"
    changed = [
        i
        for i, (a, b) in enumerate(zip(source["syscalls"], out["syscalls"], strict=True))
        if a != b
    ]
    assert changed == [1]
    assert out["syscalls"][1] == {"names": ["clone3"], "action": "SCMP_ACT_ALLOW"}
    assert len(out["syscalls"]) == len(source["syscalls"])
    assert {k: v for k, v in out.items() if k != "syscalls"} == {
        k: v for k, v in source.items() if k != "syscalls"
    }


def _with(rule: dict[str, Any]) -> dict[str, Any]:
    return inspect_doc([{"names": ["read"], "action": "SCMP_ACT_ALLOW"}, rule])


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        (inspect_doc([{"names": ["read"], "action": "SCMP_ACT_ALLOW"}]), "found 0"),
        (
            inspect_doc(
                [
                    {"names": ["clone3"], "action": "SCMP_ACT_ERRNO", "errnoRet": 38},
                    {"names": ["clone3"], "action": "SCMP_ACT_ERRNO", "errnoRet": 38},
                ]
            ),
            "found 2",
        ),
        (
            _with({"names": ["clone3", "unshare"], "action": "SCMP_ACT_ERRNO", "errnoRet": 38}),
            "grouped",
        ),
        (_with({"names": ["clone3"], "action": "SCMP_ACT_ERRNO", "errnoRet": 1}), "errnoRet"),
        (_with({"names": ["clone3"], "action": "SCMP_ACT_ERRNO"}), "errnoRet"),
        (_with({"names": ["clone3"], "action": "SCMP_ACT_ALLOW", "errnoRet": 38}), "action"),
        (_with({"names": ["clone3"], "action": "SCMP_ACT_KILL", "errnoRet": 38}), "action"),
        ({"info": {"runtimeSpec": {"linux": {}}}}, "no .info.runtimeSpec.linux.seccomp"),
        ({"info": {}}, "no .info.runtimeSpec.linux.seccomp"),
        ({}, "no .info.runtimeSpec.linux.seccomp"),
        ({"info": {"runtimeSpec": {"linux": {"seccomp": {"syscalls": "x"}}}}}, "syscalls"),
    ],
    ids=[
        "no-clone3",
        "two-clone3",
        "grouped",
        "wrong-errno",
        "missing-errno",
        "wrong-action-allow",
        "wrong-action-kill",
        "no-seccomp",
        "no-runtimespec",
        "empty",
        "bad-syscalls",
    ],
)
def test_unexpected_input_fails_closed(doc: dict[str, Any], message: str) -> None:
    with pytest.raises(derive_profile.DeriveError, match=message):
        derive_profile.derive(doc)


def run_cli(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True, check=False
    )


def test_cli_prints_the_profile_and_only_the_clone3_rule_differs() -> None:
    doc = inspect_doc()
    got = run_cli(json.dumps(doc))
    assert got.returncode == 0, got.stderr
    out = json.loads(got.stdout)
    source = doc["info"]["runtimeSpec"]["linux"]["seccomp"]
    assert out["syscalls"][1]["action"] == "SCMP_ACT_ALLOW"
    assert "errnoRet" not in out["syscalls"][1]
    for i in (0, 2, 3):
        assert out["syscalls"][i] == source["syscalls"][i]


def test_cli_exits_non_zero_with_a_message_on_bad_input() -> None:
    got = run_cli(json.dumps(inspect_doc([])))
    assert got.returncode == 1
    assert got.stdout == ""
    assert "derive_profile:" in got.stderr
    assert run_cli("not json").returncode == 1
