"""PoC-5 on kind, the sidecar lane: ADR-001 hard requirement 1 ("only the chassis holds
credentials") for LiteLLM, the MCP gateway, Valkey, and MinIO (T21; plan sections 2.10, 2.11).

Exit criterion 3: every internal service refuses a call without the chassis's credential; with it
the same call works. Each test runs the call from the sidecar workload container of `agent-echo`
(`kubectl exec`, the echo-python image's own Python, the service's literal ClusterIP), then the
paired allowed control in the same test: the same call through the chassis's proxy on
127.0.0.1:8090, and the same call from the chassis container with its own credential, read from
its env inside the container and never returned. H07 (no valid key, no model and no tool) is the
gateway half; `test_poc05_kind_tool_gateway.py` holds H07/H08 from the host.

MinIO differs: no chassis holds a MinIO key in PoC-5 (`config: memory`, security review F10), so
the network refuses the workload before MinIO's own auth does. Its control is the platform's own
credential inside the MinIO pod, and an allowed edge from the workload in the same test.

H11 (the broker) is a recorded exception, not tested here:
`notes/2026-10-02-h11-queue-exception.md`.

A kind test: marked `network`, skipped unless `POC05_KIND=1` (`poc05_conftest.py`). Run:
`POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network <this file>`.
"""

from __future__ import annotations

from typing import Any

import pytest
from poc05_kind import (
    AGENTS_NS,
    CHASSIS,
    NOT_A_KEY,
    PLATFORM_NS,
    PROXY,
    SIDECAR_POD,
    WORKLOAD,
    chat,
    kubectl,
    pod,
    probe,
    service_ip,
)

REFUSED_TCP = {"TimeoutError", "ConnectionRefusedError", "OSError"}
# The gateway lists a tool as `<server>-<tool>`; the chassis's `/mcp` passes the name on.
GLOSSARY = "fake_tools-glossary_lookup"


@pytest.fixture(scope="module")
def sidecar() -> str:
    name: str = pod(AGENTS_NS, SIDECAR_POD)["metadata"]["name"]
    return name


def in_workload(pod_name: str, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return probe(AGENTS_NS, pod_name, WORKLOAD, checks)


def in_chassis(pod_name: str, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return probe(AGENTS_NS, pod_name, CHASSIS, checks)


def test_litellm_refuses_the_workload_without_the_chassis_key(sidecar: str) -> None:
    """Criterion 3, LiteLLM: the control refuses a model call from the workload with no key and
    with a key it did not issue (401); the same call through the chassis's model proxy is 200,
    and from the chassis container with its own key, 200."""
    litellm = f"http://{service_ip(PLATFORM_NS, 'litellm')}:4000/v1"
    bare, stranger, proxied = in_workload(
        sidecar,
        [
            chat(litellm),
            chat(litellm, headers={"Authorization": f"Bearer {NOT_A_KEY}"}),
            chat(f"{PROXY}/v1"),
        ],
    )
    assert bare["status"] == 401, bare
    assert stranger["status"] == 401, stranger
    assert NOT_A_KEY not in stranger["head"]
    assert proxied["status"] == 200, proxied
    assert '"choices"' in proxied["head"]

    (own,) = in_chassis(sidecar, [chat(litellm, auth_env="LITELLM_API_KEY")])
    assert own["status"] == 200, own


def test_mcp_gateway_refuses_the_workload_without_the_chassis_key(sidecar: str) -> None:
    """Criterion 3, the MCP gateway (H07): the control refuses the MCP handshake from the workload
    with no key and with a key it did not issue (401, no tool); through the chassis's `/mcp` the
    same handshake lists the glossary tool, and the chassis's own key lists it at the gateway."""
    gateway = f"http://{service_ip(PLATFORM_NS, 'litellm')}:4000/mcp/"
    bare, stranger, proxied = in_workload(
        sidecar,
        [
            {"kind": "mcp_tools", "url": gateway},
            {
                "kind": "mcp_tools",
                "url": gateway,
                "headers": {"Authorization": f"Bearer {NOT_A_KEY}"},
            },
            {"kind": "mcp_tools", "url": f"{PROXY}/mcp"},
        ],
    )
    assert bare == {"status": 401}, bare
    assert stranger == {"status": 401}, stranger
    assert proxied["status"] == 200, proxied
    assert GLOSSARY in proxied["tools"]

    (own,) = in_chassis(
        sidecar, [{"kind": "mcp_tools", "url": gateway, "auth_env": "LITELLM_API_KEY"}]
    )
    assert own["status"] == 200, own
    assert GLOSSARY in own["tools"]


def test_valkey_refuses_the_workload_without_the_chassis_password(sidecar: str) -> None:
    """Criterion 3, Valkey: the control refuses a command from the workload with no AUTH (NOAUTH)
    and with the chassis's user name and a wrong password (WRONGPASS); the same PING after AUTH
    with the chassis's password, from the chassis container, is PONG."""
    valkey = service_ip(PLATFORM_NS, "valkey")
    (anon,) = in_workload(
        sidecar,
        [
            {
                "kind": "tcp",
                "host": valkey,
                "port": 6379,
                "resp": [["PING"], ["AUTH", "chassis", "not-the-password"], ["PING"]],
            }
        ],
    )
    assert anon["connected"] is True, anon
    first, wrong, after = anon["replies"]
    assert first.startswith("-NOAUTH"), first
    assert wrong.startswith("-WRONGPASS"), wrong
    assert after.startswith("-NOAUTH"), after

    (own,) = in_chassis(
        sidecar,
        [
            {
                "kind": "tcp",
                "host": valkey,
                "port": 6379,
                "resp": [
                    ["AUTH", {"env": "VALKEY_USERNAME"}, {"env": "VALKEY_PASSWORD"}],
                    ["PING"],
                ],
            }
        ],
    )
    assert own["replies"] == ["+OK", "+PONG"], own


def test_minio_refuses_the_workload_and_any_unsigned_call(sidecar: str) -> None:
    """Criterion 3, MinIO: the control refuses the workload at the network (no edge from any
    chassis pod, review F10: the connection to the Service and to the pod IP never opens), while
    the workload's allowed edge to LiteLLM connects in the same test. MinIO itself refuses an
    unsigned ListBuckets (403) and answers the same call signed with the platform's credential,
    inside its own pod. No chassis holds a MinIO key in PoC-5 (`config: memory`), so there is no
    "through the chassis" call to make; that half returns with the first `config: s3` chassis."""
    minio = pod(PLATFORM_NS, "minio")
    svc, pod_ip, allowed = in_workload(
        sidecar,
        [
            {"kind": "tcp", "host": service_ip(PLATFORM_NS, "minio"), "port": 9000},
            {"kind": "tcp", "host": minio["status"]["podIP"], "port": 9000},
            {"kind": "tcp", "host": service_ip(PLATFORM_NS, "litellm"), "port": 4000},
        ],
    )
    assert svc.get("error") in REFUSED_TCP, svc
    assert pod_ip.get("error") in REFUSED_TCP, pod_ip
    assert allowed.get("connected") is True, allowed

    # Inside MinIO's pod: unsigned, then signed. The pair is expanded from the container's env by
    # its shell, so it is never in kubectl's argv; mc's output is dropped, only the code is kept.
    script = (
        'curl -s -o /dev/null -w "%{http_code}\\n" http://127.0.0.1:9000/; '
        "HOME=/tmp MC_CONFIG_DIR=/tmp/.mc "
        'MC_HOST_local="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000" '
        "mc ls local/agent-configs >/dev/null 2>&1; echo $?"
    )
    got = kubectl("exec", "-n", PLATFORM_NS, minio["metadata"]["name"], "--", "sh", "-c", script)
    assert got.returncode == 0, got.stderr[-300:]
    unsigned, signed_rc = got.stdout.split()
    assert unsigned == "403", got.stdout
    assert signed_rc == "0", got.stdout
