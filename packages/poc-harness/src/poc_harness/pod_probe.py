"""The one stdlib script that checks controls from inside a pod.

`PROBE` takes a JSON list of checks in argv and prints one JSON list of results. A result holds a
status code, an error class name, a reply's first bytes, or names; never a credential. A credential
the chassis uses is named by its env variable (`auth_env`, `{"env": NAME}`), read inside the
chassis container, and never returned. `poc_harness.kind` runs it.
"""

from __future__ import annotations

PROBE = r"""
import errno, json, os, re, socket, struct, sys, urllib.error, urllib.request

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

def _redact(text, c):
    # A reply to a call made with a real credential may echo part of it (LiteLLM's 401 names
    # the key's last characters and its hash). Drop `sk-` runs, long hex runs, the value, and
    # its last four characters before anything leaves the pod.
    if not c.get("auth_env"):
        return text
    value = os.environ[c["auth_env"]]
    for part in (value, value[-4:] if len(value) >= 8 else None):
        if part:
            text = text.replace(part, "<redacted>")
    text = re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-<redacted>", text)
    return re.sub(r"[0-9a-fA-F]{32,}", "<redacted>", text)

def http(c):
    try:
        code, _, raw = _post(c["url"], c.get("body"), _headers(c), c.get("timeout", 5))
        return {"status": code, "head": _redact(raw[:120].decode(errors="replace"), c)}
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
