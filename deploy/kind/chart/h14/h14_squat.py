"""The H14 test workload (ADR-012 item 4): echo-python that binds one port at import.

The image runs `workload-a2a serve --handle h14_squat:handle`, which imports this module before
it serves. So the port is taken before the workload's agent card answers, and so before the
chassis starts (the workload is the native sidecar).

- `H14_BIND_PORT` (required) and `H14_BIND_HOST` (default 127.0.0.1): the address to hold.
  127.0.0.1:8090 is the chassis's proxy port; 0.0.0.0:8080 covers its public port on the pod IP.
- `H14_ANSWER=1`: also answer every HTTP GET on that address with 200 and `{}`, so a kubelet
  probe that reached the squatter instead of the chassis would pass.

The pod must never go Ready while a chassis port is held. A free loopback port is the paired
control: the pod goes Ready. A test image only, built by deploy/kind/chart/run.sh. It holds no
key and reaches nothing.
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from echo_python import handle

__all__ = ["handle"]

_HOST = os.environ.get("H14_BIND_HOST", "127.0.0.1")
_PORT = int(os.environ["H14_BIND_PORT"])


class _Answer(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"h14: answered {self.path}", flush=True)


_SQUAT = HTTPServer((_HOST, _PORT), _Answer, bind_and_activate=False)
_SQUAT.socket.bind((_HOST, _PORT))
_SQUAT.server_activate()
print(f"h14: holding {_HOST}:{_PORT}", flush=True)
if os.environ.get("H14_ANSWER") == "1":
    threading.Thread(target=_SQUAT.serve_forever, name="h14-answer", daemon=True).start()
