"""`workload-a2a serve --reload --reload-dir DIR` (016 H-9): the dev loop's reloader.

No server starts here: the reloader and `uvicorn.Server.run` are replaced by recorders.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from workload_a2a import cli


@pytest.fixture(autouse=True)
def no_log_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` sets up JSON logs on the root logger; keep the test run's logging as it is."""
    monkeypatch.setattr(cli, "configure_logging", lambda **_: None)


class Recorder:
    """Stands in for `watchfiles.run_process`: keeps what it was called with."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *paths: Any, **kwargs: Any) -> int:
        self.calls.append((paths, kwargs))
        return 0


def _serve_args(tmp_path: Path, *extra: str) -> list[str]:
    sock = str(tmp_path / "w.sock")
    return ["serve", "--handle", "echo_python:handle", "--uds", sock, *extra]


def test_serve_reload_runs_the_server_under_the_reloader_on_the_watch_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    recorder = Recorder()
    monkeypatch.setattr("watchfiles.run_process", recorder)
    src, extra = tmp_path / "src", tmp_path / "extra"
    src.mkdir()
    extra.mkdir()

    # Act
    cli.main(
        _serve_args(tmp_path, "--reload", "--reload-dir", str(src), "--reload-dir", str(extra))
    )

    # Assert
    [(paths, kwargs)] = recorder.calls
    assert paths == (str(src), str(extra))
    assert kwargs["target"] is cli.serve_parsed
    [child] = kwargs["args"]
    assert child.reload is False, "the child serves; it never starts a second reloader"
    assert child.handle == "echo_python:handle"
    assert child.uds == str(tmp_path / "w.sock")


def test_serve_reload_child_builds_and_runs_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder()
    monkeypatch.setattr("watchfiles.run_process", recorder)
    ran: list[uvicorn.Server] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: ran.append(self))
    cli.main(_serve_args(tmp_path, "--reload", "--reload-dir", str(tmp_path)))
    [(_, kwargs)] = recorder.calls
    assert ran == [], "the reloader process never serves; only its child does"

    kwargs["target"](*kwargs["args"])

    [server] = ran
    assert server.config.uds == str(tmp_path / "w.sock")


def test_serve_reload_needs_a_watch_dir(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--reload needs at least one --reload-dir"):
        cli.main(_serve_args(tmp_path, "--reload"))


def test_serve_reload_refuses_a_watch_dir_that_is_not_a_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(SystemExit, match=r"--reload-dir .*missing is not a directory"):
        cli.main(_serve_args(tmp_path, "--reload", "--reload-dir", str(missing)))


def test_serve_reload_dir_without_reload_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--reload-dir needs --reload"):
        cli.main(_serve_args(tmp_path, "--reload-dir", str(tmp_path)))


def test_serve_reload_without_watchfiles_says_where_it_comes_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "watchfiles", None)
    with pytest.raises(SystemExit, match="--reload needs watchfiles"):
        cli.main(_serve_args(tmp_path, "--reload", "--reload-dir", str(tmp_path)))


def test_serve_without_reload_never_starts_the_reloader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: Any, **__: Any) -> int:
        raise AssertionError("the reloader started without --reload")

    monkeypatch.setattr("watchfiles.run_process", refuse)
    ran: list[uvicorn.Server] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: ran.append(self))

    cli.main(_serve_args(tmp_path))

    assert len(ran) == 1
