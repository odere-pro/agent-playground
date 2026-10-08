"""Shared helpers for the PoC-5 kind tests that check controls from inside a pod. Not a test
module; the T21 files (`test_poc05_kind_hardreq1.py`, `test_poc05_kind_sidecar_controls.py`) and
the T22 files (`test_poc05_kind_remote_lane.py`, `test_poc05_kind_remote_controls.py`) import it.

Every check runs with `kubectl exec` of the image's own Python (T10 is dropped: no probe workload,
no attack tool). `PROBE` is one small stdlib script: it takes a JSON list of checks in argv and
prints one JSON list of results. A result holds a status code, an error class name, a reply's
first bytes, or names; never a credential. A credential the chassis uses is named by its env
variable (`auth_env`, `{"env": NAME}`), read inside the chassis container, and never returned.

Every kubectl call pins `--context kind-poc05`.
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
from typing import Any

CONTEXT = "kind-poc05"
AGENTS_NS = "poc05-agents"
PLATFORM_NS = "poc05-platform"
REMOTE_NS = "poc05-remote"
TOOLS_NS = "poc05-tools"
NODE_CONTAINER = "poc05-control-plane"
SIDECAR_POD = "agent-echo"
WORKLOAD = "workload"
CHASSIS = "chassis"
PROXY = "http://127.0.0.1:8090"
TIMEOUT_S = 90
# The chassis's Secret-sourced variables in agent-echo.yaml (names only).
CHASSIS_SECRET_ENV = frozenset({"LITELLM_API_KEY", "VALKEY_PASSWORD"})
NOT_A_KEY = "sk-poc05-not-a-real-key-0000"
METADATA_IP = ipaddress.ip_address("169.254.169.254")

PROBE = r"""
import errno, json, os, socket, struct, sys, urllib.error, urllib.request

def _headers(c):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    h.update(c.get("headers") or {})
    if c.get("auth_env"):
        h["Authorization"] = "Bearer " + os.environ[c["auth_env"]]
    return h

def _post(url, body, headers, t):
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                 headers)
    try:
        with urllib.request.urlopen(req, timeout=t) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()

def _rpc(raw):
    text = raw.decode(errors="replace")
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return json.loads(text) if text.strip().startswith("{") else {}

def http(c):
    try:
        code, _, raw = _post(c["url"], c.get("body"), _headers(c), c.get("timeout", 5))
        return {"status": code, "head": raw[:120].decode(errors="replace")}
    except Exception as e:
        return {"error": type(e).__name__}

def mcp_tools(c):
    # initialize, notifications/initialized, tools/list: the MCP handshake, names back only.
    h, t = _headers(c), c.get("timeout", 8)
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "poc05-kind-test", "version": "0"}}}
    try:
        code, rh, _ = _post(c["url"], init, h, t)
        if code != 200:
            return {"status": code}
        sid = {k.lower(): v for k, v in rh.items()}.get("mcp-session-id")
        if sid:
            h["mcp-session-id"] = sid
        _post(c["url"], {"jsonrpc": "2.0", "method": "notifications/initialized"}, h, t)
        code, _, raw = _post(c["url"], {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, h, t)
        tools = (_rpc(raw).get("result") or {}).get("tools") or []
        return {"status": code, "tools": sorted(x["name"] for x in tools)}
    except Exception as e:
        return {"error": type(e).__name__}

def _resp(args):
    parts = [os.environ[a["env"]] if isinstance(a, dict) else a for a in args]
    out = "*%d\r\n" % len(parts)
    for p in parts:
        out += "$%d\r\n%s\r\n" % (len(p.encode()), p)
    return out.encode()

def tcp(c):
    try:
        s = socket.create_connection((c["host"], c["port"]), timeout=c.get("timeout", 3))
    except Exception as e:
        return {"error": type(e).__name__}
    replies = []
    try:
        for cmd in c.get("resp") or []:
            s.sendall(_resp(cmd))
            replies.append(s.recv(200).decode(errors="replace").split("\r\n")[0])
    finally:
        s.close()
    return {"connected": True, "replies": replies}

def path(c):
    return {"exists": os.path.exists(c["path"])}

def env_names(c):
    return {"names": sorted(os.environ)}

def procs(c):
    # Every process this container can see: its cmdline's first two words, and whether its
    # environ is readable and which of the given names it holds (names only, never values).
    out = []
    for pid in sorted((p for p in os.listdir("/proc") if p.isdigit()), key=int):
        try:
            argv = open("/proc/%s/cmdline" % pid, "rb").read().split(b"\0")[:2]
        except OSError:
            continue
        try:
            env = open("/proc/%s/environ" % pid, "rb").read().split(b"\0")
            held = sorted({e.split(b"=", 1)[0].decode() for e in env} & set(c["names"]))
            readable = True
        except OSError:
            held, readable = [], False
        out.append({"pid": int(pid), "argv": [a.decode(errors="replace") for a in argv],
                    "environ_readable": readable, "secret_names": held})
    return {"procs": out}

def read(c):
    try:
        with open(c["path"], "rb") as f:
            return {"text": f.read(c.get("size", 4096)).decode(errors="replace")}
    except OSError as e:
        return {"error": errno.errorcode.get(e.errno, type(e).__name__)}

def write(c):
    # One small file; removed again when the write works. The result is the errno name.
    try:
        with open(c["path"], "w") as f:
            f.write("poc05")
    except OSError as e:
        return {"error": errno.errorcode.get(e.errno, type(e).__name__)}
    os.remove(c["path"])
    return {"written": True}

def dns(c):
    # One standard A query over UDP to a literal server address: no resolver, no DNS library.
    q = struct.pack(">HHHHHH", 0x5005, 0x0100, 1, 0, 0, 0)
    for part in c["name"].split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\0" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(c.get("timeout", 3))
    try:
        s.sendto(q, (c["server"], 53))
        raw = s.recv(512)
    except Exception as e:
        return {"error": type(e).__name__}
    finally:
        s.close()
    flags, answers = struct.unpack(">H", raw[2:4])[0], struct.unpack(">H", raw[6:8])[0]
    return {"rcode": flags & 0xF, "answers": answers}

def resolve(c):
    # The pod's own resolver (its /etc/resolv.conf), as any library call would use it.
    try:
        return {"addresses": sorted({a[4][0] for a in socket.getaddrinfo(c["name"], None)})}
    except Exception as e:
        return {"error": type(e).__name__}

KINDS = {"http": http, "mcp_tools": mcp_tools, "tcp": tcp, "path": path,
         "env_names": env_names, "procs": procs, "read": read, "write": write, "dns": dns,
         "resolve": resolve}
print(json.dumps([KINDS[c["kind"]](c) for c in json.loads(sys.argv[1])]))
"""


def kubectl(*args: str, timeout: int = TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("kubectl")
    assert exe is not None, "kubectl is not on PATH"
    return subprocess.run(
        [exe, "--context", CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def get_json(*args: str) -> Any:
    got = kubectl("get", *args, "-o", "json")
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def pod(namespace: str, name: str) -> dict[str, Any]:
    """The one Running pod of the app `name` (label `app.kubernetes.io/name`)."""
    items = get_json("pods", "-n", namespace, "-l", f"app.kubernetes.io/name={name}")["items"]
    running = [p for p in items if p["status"].get("phase") == "Running"]
    assert len(running) == 1, f"{namespace}/{name}: want one Running pod, got {len(running)}"
    found: dict[str, Any] = running[0]
    return found


def service_ip(namespace: str, name: str) -> str:
    ip: str = get_json("service", name, "-n", namespace)["spec"]["clusterIP"]
    return ip


def node_ip() -> str:
    node = get_json("nodes")["items"][0]
    ip: str = next(a["address"] for a in node["status"]["addresses"] if a["type"] == "InternalIP")
    return ip


def probe(
    namespace: str, pod_name: str, container: str, checks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Run `checks` in `container` with its own Python; one result per check, in order."""
    got = kubectl(
        "exec", "-n", namespace, pod_name, "-c", container, "--",
        "python", "-c", PROBE, json.dumps(checks),
    )  # fmt: skip
    assert got.returncode == 0, f"exec in {pod_name}/{container} failed: {got.stderr[-400:]}"
    results: list[dict[str, Any]] = json.loads(got.stdout.strip().splitlines()[-1])
    assert len(results) == len(checks)
    return results


def node_sh(script: str) -> str:
    """Run `script` with the kind node's own shell (`docker exec`); its stdout. Reads only."""
    docker = shutil.which("docker")
    assert docker is not None, "docker is not on PATH"
    got = subprocess.run(
        [docker, "exec", NODE_CONTAINER, "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert got.returncode == 0, f"node exec failed: {got.stderr[-300:]}"
    return got.stdout


def chat(url: str, **extra: Any) -> dict[str, Any]:
    """One non-streamed chat call to the fake model's route, as a probe check."""
    body = {"model": "fake-chat", "messages": [{"role": "user", "content": "hello"}]}
    return {"kind": "http", "url": f"{url}/chat/completions", "body": body, **extra}
