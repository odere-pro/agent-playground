#!/usr/bin/env python3
"""Derive the PoC-6 gVisor seccomp profile from a node's RuntimeDefault profile.

Input: the JSON of `crictl inspect <container-id>` (stdin, or the file named by the one argument).
It reads `.info.runtimeSpec.linux.seccomp`, the profile containerd generated for the container.
Output: that profile on stdout, with ONE change: the `clone3` rule's action becomes
SCMP_ACT_ALLOW and its `errnoRet` is removed. Everything else is unchanged.

Why: runsc's OCI seccomp converter ignores `errnoRet` and returns EPERM for every
SCMP_ACT_ERRNO rule (google/gvisor#14688, fix in #14721, not yet released). RuntimeDefault blocks
`clone3` with errnoRet 38 (ENOSYS) so glibc falls back to `clone`; under runsc that is EPERM and
`pthread_create` fails. See pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-lanes-b-kind.md.

Fails closed: any input that is not exactly the expected shape exits 1 with a message on stderr.
Stdlib only.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

SYSCALL = "clone3"
EXPECTED_ACTION = "SCMP_ACT_ERRNO"
EXPECTED_ERRNO = 38  # ENOSYS
NEW_ACTION = "SCMP_ACT_ALLOW"


class DeriveError(Exception):
    """The input is not the profile this script expects."""


def extract_profile(inspect: Any) -> dict[str, Any]:
    """The `.info.runtimeSpec.linux.seccomp` object of a `crictl inspect` document."""
    node = inspect
    for key in ("info", "runtimeSpec", "linux", "seccomp"):
        if not isinstance(node, dict) or key not in node:
            raise DeriveError(f"no .info.runtimeSpec.linux.seccomp in the input (missing {key!r})")
        node = node[key]
    if not isinstance(node, dict) or not isinstance(node.get("syscalls"), list):
        raise DeriveError("the seccomp profile has no syscalls list")
    return node


def clone3_rule_index(profile: dict[str, Any]) -> int:
    """The index of the one rule that names clone3; raise unless it has the expected shape."""
    hits = [
        i
        for i, rule in enumerate(profile["syscalls"])
        if isinstance(rule, dict) and SYSCALL in (rule.get("names") or [])
    ]
    if len(hits) != 1:
        raise DeriveError(f"expected exactly one rule naming {SYSCALL}, found {len(hits)}")
    rule = profile["syscalls"][hits[0]]
    if rule["names"] != [SYSCALL]:
        raise DeriveError(f"the {SYSCALL} rule is grouped with other names: {rule['names']}")
    if rule.get("action") != EXPECTED_ACTION:
        raise DeriveError(
            f"the {SYSCALL} rule action is {rule.get('action')!r}, not {EXPECTED_ACTION}"
        )
    errno = rule.get("errnoRet")
    if isinstance(errno, bool) or errno != EXPECTED_ERRNO:
        raise DeriveError(f"the {SYSCALL} rule errnoRet is {errno!r}, not {EXPECTED_ERRNO}")
    return hits[0]


def derive(inspect: Any) -> dict[str, Any]:
    """The derived profile; it differs from the input in exactly the clone3 rule."""
    source = extract_profile(inspect)
    index = clone3_rule_index(source)
    derived = copy.deepcopy(source)
    rule = derived["syscalls"][index]
    rule["action"] = NEW_ACTION
    del rule["errnoRet"]
    changed = [
        i
        for i, (a, b) in enumerate(zip(source["syscalls"], derived["syscalls"], strict=True))
        if a != b
    ]
    other = {k: v for k, v in derived.items() if k != "syscalls"}
    other_src = {k: v for k, v in source.items() if k != "syscalls"}
    if changed != [index] or other != other_src:
        raise DeriveError(
            "internal check failed: the derived profile differs in more than one rule"
        )
    return derived


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("usage: derive_profile.py [crictl-inspect.json]  (default: stdin)", file=sys.stderr)
        return 2
    try:
        if len(argv) == 2:
            inspect = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        else:
            inspect = json.load(sys.stdin)
        derived = derive(inspect)
    except (DeriveError, ValueError, OSError) as exc:
        print(f"derive_profile: {exc}", file=sys.stderr)
        return 1
    json.dump(derived, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
