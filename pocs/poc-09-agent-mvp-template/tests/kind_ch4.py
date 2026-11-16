"""The 026 CH-4 kind drill on `poc05`: what `test_poc09_kind_ch4.py` and its offline twin share.

Credentials are read from their Secrets into this process's memory only, never into argv, a file,
or the output, as `deploy/kind/poc05/run.sh with-gateway` does for the echo key. LiteLLM and Valkey
are reached through `kubectl port-forward`, which enters through the kubelet and needs no
NetworkPolicy edge (`deploy/kind/poc05/platform/network-policy.yaml`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import socket
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from poc_harness.kind import CONTEXT, PLATFORM_NS, kubectl

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "deploy/kind/poc05/platform/seed.sh"
CHART_NS = "poc05-chart"
# seed.sh CHART_SERVICES: each drill service's own tool and key prefix.
CH4 = {"ch4-a": "glossary_lookup", "ch4-b": "note_write"}
# A key or a hash: `sk-` runs, or 32 or more hex characters (the master key is 64 hex).
LEAK = re.compile(r"sk-|[0-9a-f]{32,}")
FORWARD_WAIT_S = 30


def seed(*args: str, timeout: float = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SEED), *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def leaks(text: str) -> list[str]:
    """Where `text` holds something shaped like a key or a hash; the match itself is not kept."""
    return [f"offset {m.start()}" for m in LEAK.finditer(text)]


def key_info() -> dict[str, list[dict[str, Any]]]:
    got = seed("key-info")
    assert got.returncode == 0, got.stderr[-400:]
    assert leaks(got.stdout) == []
    lines = [json.loads(line) for line in got.stdout.splitlines() if line.startswith("{")]
    return {line["alias"]: line["keys"] for line in lines}


class Cred:
    """A credential in memory. Its repr names where it came from, never the value, so a pytest
    traceback that prints arguments and locals shows no key (iteration, 2026-11-16: a plain dict
    fixture printed two drill keys)."""

    __slots__ = ("_value", "source")

    def __init__(self, value: str, source: str) -> None:
        self._value = value
        self.source = source

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Cred({self.source}, <redacted>)"

    __str__ = __repr__


def secret_value(namespace: str, name: str, key: str) -> Cred:
    """One Secret value, into memory only. An assertion names the Secret, never the value."""
    got = kubectl("get", "secret", name, "-n", namespace, "-o", f"jsonpath={{.data.{key}}}")
    assert got.returncode == 0 and got.stdout, f"no {namespace}/{name} {key}"
    return Cred(base64.b64decode(got.stdout).decode(), f"{namespace}/{name}")


@contextmanager
def forward(service: str, port: int) -> Iterator[int]:
    """`kubectl port-forward` to a `poc05-platform` Service; the local port. Stopped after."""
    exe = shutil.which("kubectl")
    assert exe is not None, "kubectl is not on PATH"
    proc = subprocess.Popen(
        [exe, "--context", CONTEXT, "-n", PLATFORM_NS, "port-forward", "--address", "127.0.0.1",
         f"svc/{service}", f":{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )  # fmt: skip
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        found = re.match(r"Forwarding from 127\.0\.0\.1:(\d+)", line)
        assert found, f"port-forward to {service} did not start: {line!r}"
        yield int(found.group(1))
    finally:
        proc.terminate()
        proc.wait(timeout=FORWARD_WAIT_S)


def models_status(litellm_port: int, key: Cred) -> int:
    """GET /v1/models with `key`: LiteLLM's answer to that key, as a status code."""
    got = httpx.get(
        f"http://127.0.0.1:{litellm_port}/v1/models",
        headers={"Authorization": f"Bearer {key.reveal()}"},
        timeout=30,
    )
    return got.status_code


def script_sha(script: str) -> str:
    """The SHA-1 Valkey names a loaded script by (what SCRIPT LOAD returns)."""
    return hashlib.sha1(script.encode()).hexdigest()


def _resp(parts: list[str]) -> bytes:
    out = f"*{len(parts)}\r\n"
    for p in parts:
        out += f"${len(p.encode())}\r\n{p}\r\n"
    return out.encode()


def valkey(valkey_port: int, user: str, password: Cred, commands: list[list[str]]) -> list[str]:
    """AUTH as `user`, then each command; one reply per command (AUTH's first): a status or error
    line (`+OK`, `-NOPERM ...`, `:1`), `$-1`, or a bulk reply's value. The drill only writes `1`
    and `2` and loads scripts, so a value is never a credential."""
    replies = []
    with socket.create_connection(("127.0.0.1", valkey_port), timeout=10) as s:
        reader = s.makefile("rb")
        for parts in [["AUTH", user, password.reveal()], *commands]:
            s.sendall(_resp(parts))
            line = reader.readline().decode(errors="replace").rstrip("\r\n")
            if line.startswith("$") and line != "$-1":
                line = reader.readline().decode(errors="replace").rstrip("\r\n")
            replies.append(line)
    return replies
