"""PoC-5 demo script, offline (plan `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 7, T26).

Exit criterion 8 (the hostile suites pass in both lanes) is shown on the cluster by `demo/demo.sh`;
this file checks the script without a cluster. Both scripts parse; the demo stops on the first
failure, pins `--context kind-poc05` on every kubectl call, sends captured output through
`run.sh redact`, and names only kind tests that exist. Its summary fails a step on a skipped or
failed check (a skip would hide a refusal), which runs here on a sample JUnit report.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "pocs/poc-05-sandboxed/demo/demo.sh"
RUN_SH = ROOT / "deploy/kind/poc05/run.sh"
TESTS = Path(__file__).resolve().parent
NODE_ID = re.compile(r"\$TESTS/(test_poc05_kind_\w+\.py)(?:::(\w+))?")
PENDING = {"test_poc05_kind_probe.py"}
"""Kind test files the demo names that are being written now; the demo fails naming them."""
NO_BASH = shutil.which("bash") is None


def _text() -> str:
    return DEMO.read_text()


@pytest.mark.skipif(NO_BASH, reason="no bash")
@pytest.mark.parametrize("script", [DEMO, RUN_SH], ids=["demo.sh", "run.sh"])
def test_poc05_script_parses(script: Path) -> None:
    out = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr


def test_poc05_demo_stops_on_the_first_failure() -> None:
    text = _text()
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "\nset -euo pipefail\n" in text
    assert DEMO.stat().st_mode & 0o111, "demo.sh is not executable"


def test_poc05_demo_pins_the_kind_context() -> None:
    """Every kubectl call goes through `kctl`, the one place that passes `--context`."""
    text = _text()
    assert "\nCONTEXT=kind-poc05\n" in text
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    calls = re.findall(r'(?<![\w"])kubectl\s+\S+.*', code)
    assert calls == ['kubectl --context "$CONTEXT" "$@"; }'], calls
    assert "kctl version --client" in code


def test_poc05_demo_redacts_what_it_captures() -> None:
    text = _text()
    assert '"$@" 2>&1 | "$RUN" redact' in text
    assert '--junitxml="$xml" "$@" 2>&1 | "$RUN" redact' in text
    assert '"$RUN" logs 2>/dev/null | "$RUN" redact' in text
    assert "with-gateway env POC05_KIND=1 uv run pytest -m network" in text


def test_poc05_demo_writes_a_dated_record_and_ends_ok() -> None:
    text = _text()
    assert '$(date -u +%F)-demo-sandboxed.md"' in text
    assert 'echo "result: every step ok"' in text
    assert text.rstrip().endswith('} 2>&1 | tee "$OUT"')


def test_poc05_demo_names_only_kind_tests_that_exist() -> None:
    found = NODE_ID.findall(_text())
    assert found
    for name, func in found:
        path = TESTS / name
        if name in PENDING and not path.exists():
            continue
        assert path.exists(), name
        if func:
            assert re.search(rf"^(async )?def {func}\(", path.read_text(), re.M), f"{name}::{func}"


def test_poc05_demo_summary_rows_name_real_tests() -> None:
    rows = re.search(r"<<'EOF'\n(.*?)\nEOF\n", _text(), re.S)
    assert rows
    kind = "\n".join(p.read_text() for p in TESTS.glob("test_poc05_kind_*.py"))
    for line in rows.group(1).splitlines():
        func = line.split("|")[0]
        assert len(line.split("|")) == 5, line
        assert re.search(rf"^(async )?def {func}\(", kind, re.M), func


def _summary(tmp_path: Path, cases: str) -> subprocess.CompletedProcess[str]:
    """Runs the demo's own CHECKS and SUMMARY_PY, cut from the script, on a sample report."""
    lines = _text().splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith("CHECKS=$("))
    end = next(i for i, line in enumerate(lines) if i > start and line == "'\n")
    (tmp_path / "vars.sh").write_text("".join(lines[start : end + 1]))
    (tmp_path / "j.xml").write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
    script = 'source vars.sh; python3 -c "$SUMMARY_PY" j.xml "$CHECKS"'
    return subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=30
    )


@pytest.mark.skipif(NO_BASH or shutil.which("python3") is None, reason="no bash or python3")
def test_poc05_demo_summary_prints_id_attempt_outcome_control(tmp_path: Path) -> None:
    out = _summary(
        tmp_path, '<testcase name="test_h19_remote_reaches_no_outside_address[caller0]"/>'
    )
    assert out.returncode == 0, out.stderr
    cid, attempt, outcome, control = (f.strip() for f in out.stdout.split("|"))
    assert cid == "H19" and "1.1.1.1" in attempt and "[caller0]" in attempt
    assert outcome == "refused: policy drop"
    assert control.startswith("control: an unpoliced caller")


@pytest.mark.skipif(NO_BASH or shutil.which("python3") is None, reason="no bash or python3")
@pytest.mark.parametrize("tag", ["skipped", "failure", "error"])
def test_poc05_demo_summary_fails_a_step_that_did_not_pass(tmp_path: Path, tag: str) -> None:
    """A skip counts as a failure: under the demo it means a check did not run."""
    out = _summary(
        tmp_path,
        '<testcase name="test_h21_root_is_read_only_and_tmp_is_writable"/>'
        f'<testcase name="test_kafka_case"><{tag} message="why"/></testcase>',
    )
    assert out.returncode == 1, out.stdout
    assert f"{tag.upper()}: why" in out.stdout
    assert "refused: EROFS" in out.stdout
