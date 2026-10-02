"""P18 step 4: what the workload can reach of daprd and of the chassis's /dapr routes.

Run inside the workload container, which shares the chassis's (and daprd's) network namespace:

    docker exec -i poc04-workload-1-1 python - \
        < pocs/poc-04-stateless-scalable/notes/dapr/probe_api.py

Stdlib only. Sends no token, or a wrong one; never reads or prints a real token.
"""

import json
import socket
import urllib.error
import urllib.request

EVENT = json.dumps({"specversion": "1.0", "id": "probe", "source": "probe", "type": "x"}).encode()
CASES = [
    (
        "daprd publish, no token",
        "POST",
        "http://127.0.0.1:3500/v1.0/publish/pubsub/agents.task.completed.v1",
        EVENT,
        None,
    ),
    (
        "daprd publish, wrong token",
        "POST",
        "http://127.0.0.1:3500/v1.0/publish/pubsub/agents.task.completed.v1",
        EVENT,
        "wrong",
    ),
    (
        "daprd state get, no token",
        "GET",
        "http://127.0.0.1:3500/v1.0/state/statestore/k",
        None,
        None,
    ),
    (
        "daprd invoke, no token",
        "POST",
        "http://127.0.0.1:3500/v1.0/invoke/echo/method/dapr/subscribe",
        b"{}",
        None,
    ),
    (
        "daprd bulk publish, no token",
        "POST",
        "http://127.0.0.1:3500/v1.0-alpha1/publish/bulk/pubsub/t",
        b"[]",
        None,
    ),
    ("daprd metadata, no token", "GET", "http://127.0.0.1:3500/v1.0/metadata", None, None),
    ("daprd healthz, no token", "GET", "http://127.0.0.1:3500/v1.0/healthz", None, None),
    (
        "daprd healthz/outbound, no token",
        "GET",
        "http://127.0.0.1:3500/v1.0/healthz/outbound",
        None,
        None,
    ),
    ("daprd shutdown, no token", "POST", "http://127.0.0.1:3500/v1.0/shutdown", b"", None),
    ("daprd metrics :9090, no token", "GET", "http://127.0.0.1:9090/metrics", None, None),
    (
        "chassis /dapr/subscribe :8090, no token",
        "GET",
        "http://127.0.0.1:8090/dapr/subscribe",
        None,
        None,
    ),
    (
        "chassis /dapr/events :8090, no token",
        "POST",
        "http://127.0.0.1:8090/dapr/events/agents.task.completed.v1",
        EVENT,
        None,
    ),
    (
        "chassis /dapr/events :8090, wrong token",
        "POST",
        "http://127.0.0.1:8090/dapr/events/agents.task.completed.v1",
        EVENT,
        "wrong",
    ),
]

for label, method, url, body, token in CASES:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("content-type", "application/json")
    if token is not None:
        req.add_header("dapr-api-token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            status, text = resp.status, resp.read(160)
    except urllib.error.HTTPError as err:
        status, text = err.code, err.read(160)
    except OSError as err:
        status, text = "error", str(err).encode()
    print(f"{label:42} {method:4} {status}  {text[:120]!r}")

for port in (3500, 50001, 50002, 9090, 8090, 9000, 8080):
    with socket.socket() as s:
        s.settimeout(2)
        result = s.connect_ex(("127.0.0.1", port))
    print(f"tcp 127.0.0.1:{port:<5} {'open' if result == 0 else f'closed ({result})'}")
