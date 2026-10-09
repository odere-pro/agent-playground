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

H11, the broker (T25; `notes/2026-10-02-h11-queue-exception.md`, "How it closes", steps 1 and 2),
runs from `agent-echo-events`, the one pod with `events: kafka`, after `run.sh kafka`. It skips
after a plain `up` only; with `POC05_KAFKA=1`, or half the pass applied, a missing deployment
fails. The echo-python image has no Kafka client, so the workload container
speaks the Kafka protocol with the standard library: ApiVersions (answered before any login, so
the broker is reachable and this is not a timeout), Metadata with no SASL (the broker closes the
connection), and SCRAM-SHA-512 with no credential (an empty user; an unknown user with an empty
password) and with the chassis's user and a guessed password. Each refusal must be the broker's
own SaslAuthenticate answer, SASL_AUTHENTICATION_FAILED (error 58); a client-side failure or a
closed connection never counts. The control: a run through that chassis
publishes its result event, read back from the topic with the chassis's own SCRAM user, inside
the chassis container (aiokafka; the password read from its env there and never returned).

A kind test: marked `network`, skipped unless `POC05_KIND=1` (`poc05_conftest.py`). Run:
`POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network <this file>`.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest
from poc05_kind import (
    AGENTS_NS,
    CHASSIS,
    NOT_A_KEY,
    PLATFORM_NS,
    POLICY_DROPPED,
    PROXY,
    SIDECAR_POD,
    WORKLOAD,
    chat,
    get_json,
    kubectl,
    pod,
    probe,
    service_ip,
)

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
    assert proxied["status"] == 200, proxied.get("status", proxied.get("error"))
    assert '"choices"' in proxied["head"]

    (own,) = in_chassis(sidecar, [chat(litellm, auth_env="LITELLM_API_KEY")])
    assert own.get("status") == 200, own.get("status", own.get("error"))


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
    assert proxied.get("status") == 200, proxied.get("status", proxied.get("error"))
    assert GLOSSARY in proxied["tools"]

    (own,) = in_chassis(
        sidecar, [{"kind": "mcp_tools", "url": gateway, "auth_env": "LITELLM_API_KEY"}]
    )
    assert own.get("status") == 200, own.get("status", own.get("error"))
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
    assert own.get("replies") == ["+OK", "+PONG"], own.get("replies", own.get("error"))


def test_minio_refuses_the_workload_and_any_unsigned_call(sidecar: str) -> None:
    """Criterion 3, MinIO: the control refuses the workload at the network (no edge from any
    chassis pod, review F10: the connection to the Service and to the pod IP never opens), while
    the workload's allowed edge to LiteLLM connects in the same test. Both drops are timeouts.
    MinIO is up on the address the workload tried: inside its pod, an unsigned call to its own
    pod IP gets MinIO's 403, and the Service's endpoints hold that pod IP (its one allowed peer,
    the `minio-init` Job, has finished). MinIO itself refuses an
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
    assert svc.get("error") == POLICY_DROPPED, svc
    assert pod_ip.get("error") == POLICY_DROPPED, pod_ip
    assert allowed.get("connected") is True, allowed

    # Inside MinIO's pod: unsigned, then signed. The pair is expanded from the container's env by
    # its shell, so it is never in kubectl's argv; mc's output is dropped, only the code is kept.
    script = (
        'curl -s -o /dev/null -w "%{http_code}\\n" http://127.0.0.1:9000/; '
        f'curl -s -o /dev/null -w "%{{http_code}}\\n" http://{minio["status"]["podIP"]}:9000/; '
        "HOME=/tmp MC_CONFIG_DIR=/tmp/.mc "
        'MC_HOST_local="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000" '
        "mc ls local/agent-configs >/dev/null 2>&1; echo $?"
    )
    got = kubectl("exec", "-n", PLATFORM_NS, minio["metadata"]["name"], "--", "sh", "-c", script)
    assert got.returncode == 0, got.stderr[-300:]
    unsigned, on_pod_ip, signed_rc = got.stdout.split()
    assert unsigned == "403", got.stdout
    assert on_pod_ip == "403", got.stdout
    slices = get_json(
        "endpointslices", "-n", PLATFORM_NS, "-l", "kubernetes.io/service-name=minio"
    )["items"]
    addresses = {a for sl in slices for e in sl.get("endpoints", []) for a in e["addresses"]}
    assert minio["status"]["podIP"] in addresses, addresses
    assert signed_rc == "0", got.stdout


# --- H11: the broker ------------------------------------------------------------------------

EVENTS_POD = "agent-echo-events"
KAFKA_PORT = 9092
KAFKA_USER = "chassis"  # KAFKA_SASL_USERNAME in agent-echo-events.yaml (a name, not a secret)
SASL_AUTHENTICATION_FAILED = 58
RESULT_TOPIC = "agents.task.completed.v1"
KAFKA_VARIABLE = "POC05_KAFKA"  # set by `run.sh kafka`: a missing broker is then a failure

# Run in the workload container with argv: host, port, user name. Prints one JSON line. Kafka's
# wire format by hand (request header v1; ApiVersions v0, Metadata v4, SaslHandshake v1,
# SaslAuthenticate v0). Broker messages are cut to 120 characters and redacted before they leave.
KAFKA_SASL = r"""
import base64, hashlib, hmac, json, os, re, socket, struct, sys
host, port, user = sys.argv[1], int(sys.argv[2]), sys.argv[3]

def enc(text):
    raw = text.encode()
    return struct.pack(">h", len(raw)) + raw

def recvn(sock, n):
    buf = b""
    while len(buf) < n:
        part = sock.recv(n - len(buf))
        if not part:
            return None
        buf += part
    return buf

def call(sock, key, version, cid, body):
    msg = struct.pack(">hhi", key, version, cid) + enc("poc05-h11") + body
    try:
        sock.sendall(struct.pack(">i", len(msg)) + msg)
        head = recvn(sock, 4)
        data = head and recvn(sock, struct.unpack(">i", head)[0])
    except (ConnectionResetError, BrokenPipeError):
        return None
    return data[4:] if data else None

def connect():
    return socket.create_connection((host, port), timeout=10)

def redact(text):
    text = re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-<redacted>", text[:120])
    return re.sub(r"[0-9a-fA-F]{32,}", "<redacted>", text)

def api_versions():
    with connect() as c:
        r = call(c, 18, 0, 1, b"")
        return {"closed": True} if r is None else {"error_code": struct.unpack(">h", r[:2])[0]}

def metadata_without_sasl():
    with connect() as c:
        r = call(c, 3, 4, 2, struct.pack(">i", -1) + b"\0")
        return {"closed": r is None}

def authenticate(c, cid, payload):
    r = call(c, 36, 0, cid, struct.pack(">i", len(payload)) + payload)
    if r is None:
        return None, "", b""
    code, mlen = struct.unpack(">hh", r[:4])
    msg = r[4:4 + mlen].decode(errors="replace") if mlen > 0 else ""
    rest = r[4 + max(mlen, 0):]
    blen = struct.unpack(">i", rest[:4])[0]
    return code, msg, rest[4:4 + blen] if blen > 0 else b""

def scram(name, password):
    with connect() as c:
        r = call(c, 17, 1, 3, enc("SCRAM-SHA-512"))
        if r is None:
            return {"closed_at": "handshake"}
        if struct.unpack(">h", r[:2])[0]:
            return {"handshake_error": struct.unpack(">h", r[:2])[0]}
        bare = "n=%s,r=%s" % (name, base64.b64encode(os.urandom(18)).decode())
        code, msg, first = authenticate(c, 4, ("n,," + bare).encode())
        if code is None:
            return {"closed_at": "first"}
        if code:
            return {"error_code": code, "step": "first", "message": redact(msg)}
        attrs = dict(p.split("=", 1) for p in first.decode().split(","))
        salted = hashlib.pbkdf2_hmac(
            "sha512", password.encode(), base64.b64decode(attrs["s"]), int(attrs["i"]))
        client_key = hmac.new(salted, b"Client Key", "sha512").digest()
        without = "c=biws,r=" + attrs["r"]
        auth = ",".join((bare, first.decode(), without)).encode()
        sig = hmac.new(hashlib.sha512(client_key).digest(), auth, "sha512").digest()
        proof = base64.b64encode(bytes(a ^ b for a, b in zip(client_key, sig))).decode()
        code, msg, _ = authenticate(c, 5, (without + ",p=" + proof).encode())
        if code is None:
            return {"closed_at": "final"}
        return {"error_code": code, "step": "final", "message": redact(msg)}

def safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        return {"error": type(e).__name__}

print(json.dumps({"api_versions": safe(api_versions), "no_sasl": safe(metadata_without_sasl),
                  "empty": safe(scram, "", ""),
                  "unknown": safe(scram, "poc05-nobody", ""),
                  "guessed": safe(scram, user, "not-the-password")}))
"""

# Run in the chassis container with argv: topic, marker. Assigns every partition of the topic at
# its end, POSTs one run with the marker as its input through the chassis's public port, then
# reads the topic with the chassis's own SCRAM user (from its env) until the marker shows up.
# Prints one JSON line: the run's HTTP status and what was found, never a credential.
RESULT_READ = r"""
import asyncio, json, os, sys, time, urllib.error, urllib.request
from aiokafka import AIOKafkaConsumer, TopicPartition
topic, marker = sys.argv[1], sys.argv[2]

def run():
    body = json.dumps({"input": {"text": marker}}).encode()
    url = "http://%s:8080/v1/run" % os.environ["POD_IP"]
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

async def main():
    consumer = AIOKafkaConsumer(
        bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"],
        security_protocol=os.environ["KAFKA_SECURITY_PROTOCOL"],
        sasl_mechanism=os.environ["KAFKA_SASL_MECHANISM"],
        sasl_plain_username=os.environ["KAFKA_SASL_USERNAME"],
        sasl_plain_password=os.environ["KAFKA_SASL_PASSWORD"],
        enable_auto_commit=False,
    )
    await consumer.start()
    try:
        await consumer.topics()
        parts = [TopicPartition(topic, p) for p in sorted(consumer.partitions_for_topic(topic))]
        consumer.assign(parts)
        await consumer.seek_to_end(*parts)
        for p in parts:
            await consumer.position(p)
        status = await asyncio.to_thread(run)
        deadline, found = time.monotonic() + 30, None
        while found is None and time.monotonic() < deadline:
            batches = await consumer.getmany(timeout_ms=1000)
            for records in batches.values():
                for rec in records:
                    if marker.encode() in (rec.value or b""):
                        found = rec
        out = {"run_status": status, "found": found is not None}
        if found is not None:
            value = json.loads(found.value)
            data = value.get("data", value)
            out.update(topic=found.topic, type=value.get("type"), status=data.get("status"),
                       agent=data.get("agent"))
        print(json.dumps(out))
    finally:
        await consumer.stop()

asyncio.run(main())
"""


@pytest.fixture(scope="module")
def events_pod() -> str:
    """The `agent-echo-events` pod. Skips only after a plain `up` (no Kafka pass at all). Fails
    when the pass ran: `POC05_KAFKA=1` (`run.sh kafka` exports it to the verbs after it), or half
    of it is there (one of the broker and the agent without the other)."""
    found = {
        name: bool(
            kubectl(
                "get", "deployment", name, "-n", ns, "--ignore-not-found", "-o", "name"
            ).stdout.strip()
        )
        for name, ns in (("kafka", PLATFORM_NS), (EVENTS_POD, AGENTS_NS))
    }
    if not any(found.values()) and os.environ.get(KAFKA_VARIABLE) != "1":
        pytest.skip("no broker on kind after a plain `up`: run `deploy/kind/poc05/run.sh kafka`")
    assert all(found.values()), f"the Kafka pass ran but deployments are missing: {found}"
    name: str = pod(AGENTS_NS, EVENTS_POD)["metadata"]["name"]
    return name


def exec_json(pod_name: str, container: str, script: str, *args: str) -> dict[str, Any]:
    got = kubectl(
        "exec", "-n", AGENTS_NS, pod_name, "-c", container, "--", "python", "-c", script, *args
    )
    assert got.returncode == 0, f"exec in {pod_name}/{container} failed: {got.stderr[-400:]}"
    seen: dict[str, Any] = json.loads(got.stdout.strip().splitlines()[-1])
    return seen


def test_kafka_refuses_the_workload_without_the_chassis_credential(events_pod: str) -> None:
    """Criterion 3, the broker (H11): from the workload container of `agent-echo-events`, which
    shares the chassis's network namespace and so every edge it has, the broker answers
    ApiVersions (reachable: a refusal here is by credential, not a policy timeout), closes a
    Metadata request sent with no SASL login, and answers error 58 to SCRAM-SHA-512 with an empty
    user, with an unknown user and an empty password, and with the chassis's user and a guessed
    password. The paired control: a run through
    that chassis publishes `agents.task.completed.v1`, and the chassis's own SCRAM user reads it
    back from the topic, matched by a marker in the run's input."""
    kafka = service_ip(PLATFORM_NS, "kafka")
    seen = exec_json(events_pod, WORKLOAD, KAFKA_SASL, kafka, str(KAFKA_PORT), KAFKA_USER)
    assert seen["api_versions"] == {"error_code": 0}, seen
    assert seen["no_sasl"] == {"closed": True}, seen
    # Each refusal is the broker's own SaslAuthenticate answer: a client-side failure or a closed
    # connection has no error code, so it never counts.
    assert seen["empty"].get("error_code") == SASL_AUTHENTICATION_FAILED, seen
    assert seen["unknown"] == {
        "error_code": SASL_AUTHENTICATION_FAILED,
        "step": "first",
        "message": seen["unknown"].get("message"),
    }, seen
    assert seen["guessed"].get("error_code") == SASL_AUTHENTICATION_FAILED, seen
    assert seen["guessed"].get("step") == "final", seen  # the user exists; the password is wrong

    marker = f"poc05-h11-{uuid.uuid4().hex[:12]}"
    own = exec_json(events_pod, CHASSIS, RESULT_READ, RESULT_TOPIC, marker)
    assert own["run_status"] == 200, own
    assert own["found"] is True, own
    assert own["topic"] == RESULT_TOPIC, own
    assert own["type"] == RESULT_TOPIC, own
    assert own["status"] == "ok", own
