"""Spawned processes: a process group each, killed on exit, normal or not."""

from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

__all__ = ["Cleanup", "Spawned", "free_port", "install_signal_handlers", "non_loopback_ip"]

TERM_WAIT_S = 3.0


def free_port() -> int:
    """A random free loopback TCP port. The port is released before it is returned, so another
    process could take it first; the kit checks that each server started."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def non_loopback_ip() -> str | None:
    """This host's own non-loopback IPv4 address, or None. Nothing is sent: a UDP `connect`
    only picks a route."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1: reserved, never routed
            address: str = sock.getsockname()[0]
        except OSError:
            return None
    return None if address.startswith("127.") or address == "0.0.0.0" else address


class Spawned:
    """One child process, started in its own session so the whole group can be signalled."""

    def __init__(
        self, name: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path, log: Path
    ):
        self.name = name
        self.log = log
        self._out = log.open("wb")
        self.proc = subprocess.Popen(
            list(argv),
            env=dict(env),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=self._out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def alive(self) -> bool:
        return self.proc.poll() is None

    def tail(self, secrets: Sequence[str] = (), lines: int = 3) -> str:
        """The last log lines, with every value in `secrets` removed."""
        try:
            text = self.log.read_text(errors="replace")
        except OSError:
            return ""
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        kept = [line.strip() for line in text.splitlines() if line.strip()]
        return " | ".join(kept[-lines:])[-400:]

    def kill(self) -> None:
        """SIGTERM the group, then SIGKILL after `TERM_WAIT_S`. Safe to call twice."""
        try:
            pgid = os.getpgid(self.proc.pid)
        except ProcessLookupError:
            pgid = None
        if pgid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=TERM_WAIT_S)
        except subprocess.TimeoutExpired:
            if pgid is not None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(pgid, signal.SIGKILL)
            self.proc.wait()
        if pgid is not None:  # a grandchild may outlive the leader
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGKILL)
        self._out.close()


class Cleanup:
    """Everything to undo at exit: processes first, then temp dirs. Registered with `atexit`."""

    def __init__(self) -> None:
        self.procs: list[Spawned] = []
        self.dirs: list[Path] = []
        atexit.register(self.run)

    def tempdir(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="bakeoff-"))
        self.dirs.append(path)
        return path

    def spawn(
        self, name: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path, log: Path
    ) -> Spawned:
        child = Spawned(name, argv, env, cwd, log)
        self.procs.append(child)
        return child

    def release(self, child: Spawned) -> None:
        child.kill()
        if child in self.procs:
            self.procs.remove(child)

    def remove_dir(self, path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)
        if path in self.dirs:
            self.dirs.remove(path)

    def run(self) -> None:
        for child in list(self.procs):
            self.release(child)
        for path in list(self.dirs):
            self.remove_dir(path)


def install_signal_handlers() -> None:
    """Turn SIGTERM and SIGHUP into a normal exit, so `atexit` runs and the children die."""

    def _exit(signum: int, _frame: object) -> None:
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _exit)
