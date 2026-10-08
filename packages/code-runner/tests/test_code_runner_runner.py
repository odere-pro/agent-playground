"""The child-process runner: limits, the fresh directory, the minimal environment (plan 2.8).

Each test starts at most one tiny child Python process, bounded by a short timeout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from code_runner.runner import Limits, RunnerError, child_env, run_python

FAST = Limits(output_cap_bytes=1024)


def _entries(path: Path) -> list[Path]:
    return list(path.iterdir())


def _gone(path: str, parent: Path) -> bool:
    return Path(path).resolve().parent == parent.resolve() and not Path(path).exists()


async def test_code_runner_returns_stdout_and_exit_zero(tmp_path: Path) -> None:
    result = await run_python("print('hello')", 5, limits=FAST, tmp_root=tmp_path)
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.truncated is False


async def test_code_runner_reports_stderr_and_exit_code(tmp_path: Path) -> None:
    code = "import sys\nsys.stderr.write('bad')\nsys.exit(3)"
    result = await run_python(code, 5, limits=FAST, tmp_root=tmp_path)
    assert result.exit_code == 3
    assert result.stderr == "bad"


async def test_code_runner_uncaught_exception_is_a_result_not_an_error(tmp_path: Path) -> None:
    result = await run_python("raise ValueError('boom')", 5, limits=FAST, tmp_root=tmp_path)
    assert result.exit_code == 1
    assert "ValueError: boom" in result.stderr


async def test_code_runner_kills_the_child_at_the_timeout(tmp_path: Path) -> None:
    result = await run_python("while True:\n    pass", 0.5, limits=FAST, tmp_root=tmp_path)
    assert result.timed_out is True
    assert result.exit_code != 0


async def test_code_runner_cuts_each_stream_at_the_cap(tmp_path: Path) -> None:
    code = "import sys\nsys.stdout.write('a' * 5000)\nsys.stderr.write('b' * 5000)"
    result = await run_python(code, 5, limits=FAST, tmp_root=tmp_path)
    assert result.stdout == "a" * 1024
    assert result.stderr == "b" * 1024
    assert result.truncated is True
    assert result.exit_code == 0


async def test_code_runner_child_env_holds_nothing_from_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODE_RUNNER_TEST_MARKER", "must-not-leak")
    code = "import os\nprint(sorted(os.environ))\nprint(os.environ.get('CODE_RUNNER_TEST_MARKER'))"
    result = await run_python(code, 5, limits=FAST, tmp_root=tmp_path)
    assert "must-not-leak" not in result.stdout
    names = set(child_env(str(tmp_path)))
    assert names <= {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}


async def test_code_runner_runs_in_a_fresh_directory_removed_afterwards(tmp_path: Path) -> None:
    code = "import os\nopen('scratch.txt', 'w').write('x')\nprint(os.getcwd())"
    first = await run_python(code, 5, limits=FAST, tmp_root=tmp_path)
    second = await run_python(code, 5, limits=FAST, tmp_root=tmp_path)
    assert first.exit_code == 0, first.stderr
    cwd1, cwd2 = first.stdout.strip(), second.stdout.strip()
    assert cwd1 != cwd2
    assert _gone(cwd1, tmp_path)
    assert _gone(cwd2, tmp_path)
    assert _entries(tmp_path) == []


async def test_code_runner_child_is_isolated_mode_without_site(tmp_path: Path) -> None:
    code = "import sys\nprint(sys.flags.isolated, sys.flags.no_site)"
    result = await run_python(code, 5, limits=FAST, tmp_root=tmp_path)
    assert result.stdout.strip() == "1 1"


async def test_code_runner_child_has_rlimits(tmp_path: Path) -> None:
    limits = Limits(open_files=32, file_size_bytes=4096)
    code = (
        "import resource as r\n"
        "print(r.getrlimit(r.RLIMIT_NOFILE)[0], r.getrlimit(r.RLIMIT_FSIZE)[0],"
        " r.getrlimit(r.RLIMIT_CPU)[0])"
    )
    result = await run_python(code, 2, limits=limits, tmp_root=tmp_path)
    assert result.stdout.split() == ["32", "4096", "3"]


async def test_code_runner_refuses_oversized_code(tmp_path: Path) -> None:
    limits = Limits(max_code_bytes=16)
    with pytest.raises(RunnerError) as info:
        await run_python("print('x' * 100000)", 5, limits=limits, tmp_root=tmp_path)
    assert info.value.code == "bad_arguments"
    assert _entries(tmp_path) == []


@pytest.mark.parametrize("timeout_s", [0, -1, 11])
async def test_code_runner_refuses_a_timeout_out_of_bounds(
    timeout_s: float, tmp_path: Path
) -> None:
    with pytest.raises(RunnerError) as info:
        await run_python("print(1)", timeout_s, limits=FAST, tmp_root=tmp_path)
    assert info.value.code == "bad_arguments"


async def test_code_runner_missing_interpreter_is_run_failed(tmp_path: Path) -> None:
    with pytest.raises(RunnerError) as info:
        await run_python(
            "print(1)", 5, limits=FAST, tmp_root=tmp_path, python=str(tmp_path / "no-python")
        )
    assert info.value.code == "run_failed"
    assert _entries(tmp_path) == []
