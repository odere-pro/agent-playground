"""PoC-5 on kind, the sidecar lane: ADR-001 hard requirement 1 ("only the chassis holds
credentials") for LiteLLM, the MCP gateway, Valkey, and MinIO (T21; plan sections 2.10, 2.11).

Exit criterion 3: every internal service refuses a call without the chassis's credential; with it
the same call works. Each test runs the call from the sidecar workload container of `agent-echo`
(`kubectl exec`, the echo-python image's own Python, the service's literal ClusterIP), then the
paired allowed control in the same test: the same call through the chassis's proxy on
127.0.0.1:8090, and the same call from the chassis container with its own credential, read from
its env inside the container and never returned. H07 (no valid key, no model and no tool) is the
gateway half; `test_poc05_kind_tool_gateway.py` holds H07/H08 from the host.

MinIO, the config store (H12): `agent-echo`'s chassis reads its config document from MinIO
(`config: minio`) with its own user, `chassis-echo`, whose one policy is `s3:GetObject` on
`agents/echo.yaml`. The workload rides the same edge, so MinIO's SigV4 check is the control: a
GET of that document with no signature, with a made-up key pair, and with the chassis's user and a
guessed secret each get MinIO's own 403. The allowed half: a run through the chassis reports the
document's version (`versions.config`), and the chassis's own key reads the document and nothing
else. Every pod without the edge is still dropped by the network.

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

A kind test: marked `network`, skipped unless `KIND_DRILLS=1` (`poc_harness.drills`). Run:
`KIND_DRILLS=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network <this file>`.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any

import pytest
import yaml
from chassis.server.config_loader import parse_document
from poc_harness.drills import KIND_SANDBOX
from poc_harness.kind import (
    AGENTS_NS,
    CHASSIS,
    NOT_A_KEY,
    PLATFORM_NS,
    POLICY_DROPPED,
    PROXY,
    SIDECAR_POD,
    WORKLOAD,
    chat,
    kubectl,
    pod,
    pods,
    probe,
    service_ip,
)
from poc_harness.paths import KIND_POC05

pytestmark = KIND_SANDBOX

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


def exec_json(pod_name: str, container: str, script: str, *args: str) -> dict[str, Any]:
    got = kubectl(
        "exec", "-n", AGENTS_NS, pod_name, "-c", container, "--", "python", "-c", script, *args
    )
    assert got.returncode == 0, f"exec in {pod_name}/{container} failed: {got.stderr[-400:]}"
    seen: dict[str, Any] = json.loads(got.stdout.strip().splitlines()[-1])
    return seen


def test_litellm_refuses_the_workload_without_the_chassis_key(sidecar: str) -> None:
    """Criterion 3: "From the workload container, every internal service refuses a call without the
    chassis's credential. Through the chassis, the same calls succeed."

    Criterion 3, LiteLLM: the control refuses a model call from the workload with no key and
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
    """Criterion 3: "From the workload container, every internal service refuses a call without the
    chassis's credential. Through the chassis, the same calls succeed."

    Criterion 3, the MCP gateway (H07): the control refuses the MCP handshake from the workload
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
    """Criterion 3: "From the workload container, every internal service refuses a call without the
    chassis's credential. Through the chassis, the same calls succeed."

    Criterion 3, Valkey: the control refuses a command from the workload with no AUTH (NOAUTH)
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


# --- H12: the config store -------------------------------------------------------------------

MINIO_PORT = 9000
MINIO_USER = "chassis-echo"  # CONFIG_S3_ACCESS_KEY in agent-echo.yaml (a name, not a secret)
BUCKET = "agent-configs"
AGENT = "echo"
DOCUMENT = f"agents/{AGENT}.yaml"
OTHER_DOCUMENT = "agents/echo-remote.yaml"  # outside the user's one path
STORE_DOCUMENT = KIND_POC05 / "platform/agent-configs/echo.yaml"
EVENTS_DEPLOYMENT = "agent-echo-events"

# Run with argv: host, port, bucket, object key, another object key, user name, and a mode.
# Standard library only, so it stays small next to the chassis inside its 256Mi container (a
# second process with the chassis package and minio-py loaded got the container OOM-killed on
# kind). Mode `refused`, in the workload container: a GET of the object with no signature, then
# signed (SigV4) with a made-up key pair, and with the chassis's user name and a guessed secret.
# Mode `own`, in the chassis container, with the chassis's own pair from its env: the GET (200, and
# the SHA-256 of what came back), the other object, a PUT of the same bytes (harmless if it were
# allowed), and a listing; then one run through the chassis's public port. A 403 with an S3 code
# other than SignatureDoesNotMatch shows the signature was good, so the signer is checked too.
# Prints one JSON line: statuses, S3 error codes, a hash, and a version; never a credential.
S3_CALLS = r"""
import datetime, hashlib, hmac, http.client, json, os, re, sys
import urllib.error, urllib.parse, urllib.request
host, port, bucket, key, other, user, mode = sys.argv[1], int(sys.argv[2]), *sys.argv[3:8]
EMPTY = hashlib.sha256(b"").hexdigest()
SIGNED = "host;x-amz-content-sha256;x-amz-date"

def mac(k, msg):
    return hmac.new(k, msg.encode(), hashlib.sha256).digest()

def signed(method, path, query, payload, access, secret):
    now = datetime.datetime.now(datetime.timezone.utc)
    amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    canonical = "\n".join([method, path, query, "host:%s:%d" % (host, port),
                           "x-amz-content-sha256:" + payload, "x-amz-date:" + amz, "", SIGNED,
                           payload])
    scope = day + "/us-east-1/s3/aws4_request"
    text = "\n".join(["AWS4-HMAC-SHA256", amz, scope,
                      hashlib.sha256(canonical.encode()).hexdigest()])
    k = mac(("AWS4" + secret).encode(), day)
    for part in ("us-east-1", "s3", "aws4_request"):
        k = mac(k, part)
    sig = hmac.new(k, text.encode(), hashlib.sha256).hexdigest()
    auth = "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (
        access, scope, SIGNED, sig)
    return {"x-amz-date": amz, "x-amz-content-sha256": payload, "Authorization": auth}

def call(method, path, query="", body=b"", pair=None):
    try:
        headers = {} if pair is None else signed(
            method, path, query, hashlib.sha256(body).hexdigest(), *pair)
        conn = http.client.HTTPConnection(host, port, timeout=10)
        conn.request(method, path + ("?" + query if query else ""), body=body or None,
                     headers=headers)
        r = conn.getresponse()
        data = r.read(262144)
        out = {"status": r.status}
        if r.status >= 300:
            code = re.search(r"<Code>([A-Za-z]+)</Code>", data.decode(errors="replace"))
            out["code"] = code.group(1) if code else None
        return out, data
    except Exception as e:
        return {"error": type(e).__name__}, b""

def run():
    req = urllib.request.Request("http://%s:8080/v1/run" % os.environ["POD_IP"],
                                 json.dumps({"input": {"text": "poc05-h12"}}).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())["versions"]["config"]
    except urllib.error.HTTPError as e:
        return e.code, None

doc, elsewhere = "/%s/%s" % (bucket, key), "/%s/%s" % (bucket, other)
if mode == "own":
    pair = os.environ["CONFIG_S3_ACCESS_KEY"], os.environ["CONFIG_S3_SECRET_KEY"]
    got, data = call("GET", doc, pair=pair)
    got["sha256"] = hashlib.sha256(data).hexdigest() if got.get("status") == 200 else None
    listing = "list-type=2&prefix=" + urllib.parse.quote(key.rsplit("/", 1)[0] + "/", safe="")
    status, served = run()
    print(json.dumps({"own": got, "other": call("GET", elsewhere, pair=pair)[0],
                      "put": call("PUT", doc, body=data, pair=pair)[0],
                      "list": call("GET", "/" + bucket, listing, pair=pair)[0],
                      "run_status": status, "served": served}))
else:
    print(json.dumps({"unsigned": call("GET", doc)[0],
                      "stranger": call("GET", doc, pair=("poc05-nobody", "not-the-secret-0000"))[0],
                      "guessed": call("GET", doc, pair=(user, "not-the-secret-0000"))[0]}))
"""


def _store_document() -> tuple[str, str]:
    """The store document in the repo, which minio-init copies byte for byte: its SHA-256 and the
    version the chassis must report for it (`parse_document`, the chassis's own code)."""
    raw = STORE_DOCUMENT.read_bytes()
    return hashlib.sha256(raw).hexdigest(), str(parse_document(yaml.safe_load(raw)).version)


def _chassis_restarts(pod_name: str) -> int:
    statuses = kubectl(
        "get",
        "pod",
        pod_name,
        "-n",
        AGENTS_NS,
        "-o",
        "jsonpath={.status.initContainerStatuses[?(@.name=='chassis')].restartCount}",
    )
    assert statuses.returncode == 0, statuses.stderr[-300:]
    return int(statuses.stdout.strip() or 0)


def test_minio_refuses_the_workload_without_the_chassis_key(sidecar: str) -> None:
    """Criterion 3: "From the workload container, every internal service refuses a call without the
    chassis's credential. Through the chassis, the same calls succeed."

    Criterion 3, MinIO (H12): from the workload container of `agent-echo`, which rides its
    chassis's edge to MinIO, a GET of the chassis's config document gets MinIO's own 403 with no
    signature (AccessDenied), signed with a made-up key pair (InvalidAccessKeyId), and signed
    with the chassis's user and a guessed secret (SignatureDoesNotMatch). The paired control,
    inside the chassis container: the same signed GET with the chassis's own pair is 200 and
    returns the repo's store document byte for byte, while a document outside its path, a write,
    and a listing are AccessDenied; and a run through the chassis is 200 and reports that
    document's version (`versions.config`, `minio-1+<hash>`), not the bootstrap's. The chassis
    container does not restart during the test. The drop control: the pods
    without the edge (`chassis-echo-remote`, and `agent-echo-events` after the Kafka pass) still
    time out on MinIO while their LiteLLM edge connects in the same probe."""
    restarts = _chassis_restarts(sidecar)
    minio = service_ip(PLATFORM_NS, "minio")
    call = (minio, str(MINIO_PORT), BUCKET, DOCUMENT, OTHER_DOCUMENT, MINIO_USER)
    refused = exec_json(sidecar, WORKLOAD, S3_CALLS, *call, "refused")
    assert refused["unsigned"] == {"status": 403, "code": "AccessDenied"}, refused
    assert refused["stranger"] == {"status": 403, "code": "InvalidAccessKeyId"}, refused
    assert refused["guessed"] == {"status": 403, "code": "SignatureDoesNotMatch"}, refused

    digest, version = _store_document()
    own = exec_json(sidecar, CHASSIS, S3_CALLS, *call, "own")
    assert own["own"] == {"status": 200, "sha256": digest}, own
    assert own["other"] == {"status": 403, "code": "AccessDenied"}, own
    assert own["put"] == {"status": 403, "code": "AccessDenied"}, own
    assert own["list"] == {"status": 403, "code": "AccessDenied"}, own
    assert own["run_status"] == 200, own
    assert own["served"] == version, (own, version)
    assert version.startswith("minio-1+"), version
    assert _chassis_restarts(sidecar) == restarts, "the checks restarted the chassis"

    litellm = service_ip(PLATFORM_NS, "litellm")
    # Each pod with no MinIO edge, and whether its policy lets it reach LiteLLM. agent-echo-events
    # calls neither LiteLLM nor Valkey (agent-echo-events.yaml), so its policy drops both.
    dropped = [("chassis-echo-remote", CHASSIS, True)]
    if pods(AGENTS_NS, EVENTS_DEPLOYMENT):
        dropped.append((EVENTS_DEPLOYMENT, WORKLOAD, False))
    for app, container, reaches_litellm in dropped:
        name = pod(AGENTS_NS, app)["metadata"]["name"]
        to_minio, to_litellm = probe(
            AGENTS_NS,
            name,
            container,
            [
                {"kind": "tcp", "host": minio, "port": MINIO_PORT},
                {"kind": "tcp", "host": litellm, "port": 4000},
            ],
        )
        assert to_minio.get("error") == POLICY_DROPPED, (app, to_minio)
        if reaches_litellm:
            assert to_litellm.get("connected") is True, (app, to_litellm)
        else:
            assert to_litellm.get("error") == POLICY_DROPPED, (app, to_litellm)


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

# Run in the chassis container with argv: topic, marker. Subscribes (no group) and fixes every
# partition's position at its end, POSTs one run with the marker as its input through the
# chassis's public port, then reads the topic with the chassis's own SCRAM user (from its env)
# until the marker shows up.
# Prints one JSON line: the run's HTTP status and what was found, never a credential.
RESULT_READ = r"""
import asyncio, json, os, sys, time, urllib.error, urllib.request
from aiokafka import AIOKafkaConsumer
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
        consumer.subscribe([topic])  # no group: every partition, offsets at the end (latest)
        deadline = time.monotonic() + 20
        while not consumer.assignment() and time.monotonic() < deadline:
            await consumer.getmany(timeout_ms=200)
        parts = sorted(consumer.assignment())
        if not parts:
            raise RuntimeError("no partition of the topic was assigned")
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


def test_kafka_refuses_the_workload_without_the_chassis_credential(events_pod: str) -> None:
    """Criterion 3: "From the workload container, every internal service refuses a call without the
    chassis's credential. Through the chassis, the same calls succeed."

    Criterion 3, the broker (H11): from the workload container of `agent-echo-events`, which
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
