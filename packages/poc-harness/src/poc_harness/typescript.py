"""The TypeScript echo workload (`packages/workloads/echo-typescript`) as a child process.

`typescript_node` skips, pointing at `npm ci`, when the workload's `node_modules` is absent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from poc_harness.paths import ROOT, TYPESCRIPT_ECHO
from poc_harness.scenario import CHASSIS_PROXY_URL, TYPESCRIPT_URL
from poc_harness.sockets import SOCKET_DIR_PREFIX, on_unix_socket

CARD_PATH = "/.well-known/agent-card.json"
START_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 10.0


def typescript_node() -> str:
    """The `node` binary, once `dist` is built and no older than `src`. Skips, pointing at
    `npm ci`, when the workload's `node_modules` is absent (CI does not install it yet).
    """
    if not (TYPESCRIPT_ECHO / "node_modules").is_dir():
        pytest.skip(
            f"{TYPESCRIPT_ECHO.relative_to(ROOT)}/node_modules is absent; run `npm ci` there"
        )
    node, npm = shutil.which("node"), shutil.which("npm")
    if node is None or npm is None:
        pytest.skip("node_modules is there but Node is not on PATH; install Node 24, then `npm ci`")
    main = TYPESCRIPT_ECHO / "dist/src/main.js"
    newest = max(path.stat().st_mtime for path in (TYPESCRIPT_ECHO / "src").glob("*.ts"))
    if not main.exists() or main.stat().st_mtime < newest:
        subprocess.run([npm, "run", "build"], cwd=TYPESCRIPT_ECHO, check=True, capture_output=True)
    return node


def _listen_on(
    uds: str | None, port: int | None, env: Mapping[str, str] | None
) -> tuple[str, dict[str, str], httpx.Client]:
    """The base URL, the child's environment, and a probe client for one of `uds` and `port`."""
    child_env = {**os.environ, **(env or {})}
    if uds is not None:
        child_env["UDS"] = uds
        transport = httpx.HTTPTransport(uds=uds)
        return TYPESCRIPT_URL, child_env, httpx.Client(transport=transport, base_url=TYPESCRIPT_URL)
    base_url = f"http://127.0.0.1:{port}"
    child_env.update(HOST="127.0.0.1", PORT=str(port))
    return base_url, child_env, httpx.Client(base_url=base_url, trust_env=False)


def _wait_for_card(proc: subprocess.Popen[str], probe: httpx.Client) -> None:
    """Poll the agent card until it answers 200; kill the child and raise if it never does."""
    deadline = time.monotonic() + START_TIMEOUT_S
    while True:
        try:
            if probe.get(CARD_PATH, timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline or proc.poll() is not None:
            proc.kill()
            out = proc.communicate(timeout=5)[0]
            raise RuntimeError(f"echo-typescript did not serve its agent card:\n{out}")
        time.sleep(0.1)


def _stop(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@contextmanager
def typescript_echo(
    *,
    uds: str | None = None,
    port: int | None = None,
    env: Mapping[str, str] | None = None,
) -> Iterator[str]:
    """`node dist/src/main.js` in packages/workloads/echo-typescript; yields its base URL once
    the agent card answers. With `uds` it listens on that Unix socket (`UDS`), no TCP, and the URL
    only names the card's host (`TYPESCRIPT_URL`); with `port` it binds TCP on loopback, so only
    a `network` test passes it. `env` adds variables, for example `CHASSIS_MODEL_UDS`.
    """
    if (uds is None) == (port is None):
        raise ValueError("pass exactly one of uds and port")
    node = typescript_node()
    base_url, child_env, probe = _listen_on(uds, port, env)
    proc = subprocess.Popen(
        [node, "dist/src/main.js"],
        cwd=TYPESCRIPT_ECHO,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_card(proc, probe)
        yield base_url
    finally:
        probe.close()
        _stop(proc)


@contextmanager
def typescript_echo_on_unix_sockets(fake_model: Any) -> Iterator[tuple[str, str]]:
    """The TypeScript echo on a Unix socket, its model calls going over another Unix socket to
    `fake_model` (`CHASSIS_MODEL_UDS`). No TCP anywhere. Yields `(url, uds)` for
    `SidecarConnector.setup`.
    """
    with on_unix_socket(fake_model, "model.sock") as model_uds:
        folder = tempfile.mkdtemp(prefix=SOCKET_DIR_PREFIX)
        uds = str(Path(folder, "ts.sock"))
        env = {"CHASSIS_MODEL_URL": f"{CHASSIS_PROXY_URL}/v1", "CHASSIS_MODEL_UDS": model_uds}
        try:
            with typescript_echo(uds=uds, env=env) as url:
                yield url, uds
        finally:
            shutil.rmtree(folder, ignore_errors=True)
