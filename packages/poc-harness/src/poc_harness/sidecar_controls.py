"""Helpers for the sidecar-lane controls on kind (PoC-5), shared with the PoC-6b kind tests."""

from __future__ import annotations

import ipaddress
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest

from poc_harness.kind import (
    METADATA_IP,
    PLATFORM_NS,
    get_json,
    node_run,
    service_ip,
)

SA_DIR = "/var/run/secrets/kubernetes.io"


METADATA_MARK = "poc05-metadata-stub"


METADATA_PID = "/tmp/poc05-metadata.pid"


# A one-line HTTP answer per connection; nothing else. Bound to the metadata address only.
METADATA_LISTENER = (
    "use IO::Socket::INET; "
    f'my $s = IO::Socket::INET->new(LocalAddr => "{METADATA_IP}", LocalPort => 80, '
    "Listen => 5, ReuseAddr => 1) or die; "
    "while (my $c = $s->accept) { my $b; $c->recv($b, 1024); "
    f'print $c "HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n{METADATA_MARK}\n"; '
    "close $c }"
)


NODE_CONTAINER = "poc05-control-plane"


API_PORT = 6443


def container(spec: dict[str, Any], name: str) -> dict[str, Any]:
    every = spec["spec"].get("initContainers", []) + spec["spec"]["containers"]
    found: dict[str, Any] = next(c for c in every if c["name"] == name)
    return found


def secret_refs(c: dict[str, Any], volumes: dict[str, dict[str, Any]]) -> list[str]:
    """Where container `c` gets anything from a Secret: env, envFrom, or a mounted volume."""
    refs = [e["name"] for e in c.get("env", []) if "secretKeyRef" in e.get("valueFrom", {})]
    refs += ["envFrom" for e in c.get("envFrom", []) if "secretRef" in e]
    for m in c.get("volumeMounts", []):
        v = volumes.get(m["name"], {})
        projected = v.get("projected", {}).get("sources", [])
        if "secret" in v or any("secret" in s or "serviceAccountToken" in s for s in projected):
            refs.append(f"volume:{m['name']}")
    return refs


def allowed_edge() -> dict[str, Any]:
    """The workload's allowed control: LiteLLM on 4000 (the shared pod's egress)."""
    return {"kind": "tcp", "host": service_ip(PLATFORM_NS, "litellm"), "port": 4000}


def node_reaches_its_api(ip: str) -> str:
    """The node container's own curl to https://<node IP>:6443/livez (anonymous, allowed by the
    default `system:public-info-viewer` role)."""
    docker = shutil.which("docker")
    assert docker is not None, "docker is not on PATH"
    got = subprocess.run(
        [
            docker,
            "exec",
            NODE_CONTAINER,
            "curl",
            "-sk",
            "-m",
            "5",
            f"https://{ip}:{API_PORT}/livez",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return got.stdout.strip()


def policy_peers_covering(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> list[str]:
    """Every egress rule in a live PoC-5 NetworkPolicy that could allow `address`: an `ipBlock`
    whose CIDR holds it (minus its `except`), or a rule with no `to` (any destination)."""
    found = []
    for policy in get_json("networkpolicy", "-A")["items"]:
        meta = policy["metadata"]
        if not meta["namespace"].startswith("poc05-"):
            continue
        where = f"{meta['namespace']}/{meta['name']}"
        for rule in policy["spec"].get("egress", []):
            if not rule.get("to"):
                found.append(f"{where}: egress rule with no `to`")
            for peer in rule.get("to", []):
                block = peer.get("ipBlock")
                if block is None:
                    continue
                excluded = any(address in ipaddress.ip_network(e) for e in block.get("except", []))
                if address in ipaddress.ip_network(block["cidr"]) and not excluded:
                    found.append(f"{where}: ipBlock {block['cidr']}")
    return found


@pytest.fixture
def metadata_listener() -> Iterator[str]:
    """A stand-in at 169.254.169.254:80 on the kind node for the length of one test: the address
    on the node's loopback and a perl listener. Both are removed after, also on failure."""
    stop = (
        f"[ -f {METADATA_PID} ] && kill $(cat {METADATA_PID}) 2>/dev/null; rm -f {METADATA_PID}; "
        f"ip addr del {METADATA_IP}/32 dev lo 2>/dev/null; true"
    )
    node_run(stop)
    node_run(
        f"ip addr replace {METADATA_IP}/32 dev lo && "
        f"(nohup perl -e {shlex.quote(METADATA_LISTENER)} >/dev/null 2>&1 & "
        f"echo $! > {METADATA_PID})"
    )
    try:
        reply = node_run(f"sleep 1; curl -s -m 3 http://{METADATA_IP}/")
        assert reply.strip() == METADATA_MARK, reply
        yield METADATA_MARK
    finally:
        node_run(stop)
        left = node_run(f"ip -4 addr show dev lo | grep -c {METADATA_IP} || true")
        assert left.strip() == "0", f"{METADATA_IP} left on the node's loopback"
