"""PoC-5 records, offline (plan `docs/plans/2026-10-02-poc-05-sandboxed.md`, T30).

Exit criterion 9: gVisor works for every engine that runs in the `remote` lane (or the exceptions
are listed), and its latency and memory overhead are measured. The record is
`notes/2026-10-09-gvisor-overhead.md`: a markdown table with a `gvisor` row and a `runc` row for
each remote engine (`echo-python`, `code-runner`), each with a number in a column whose header
says `ms` and one whose header says `MiB` (the table `notes/spike/q9-overhead-engines.sh`
prints), and a paragraph that names TypeScript as an exception.

Exit criterion 10: what the chassis cannot see or control in the `remote` lane is listed. The
record is `notes/2026-10-02-blind-spots.md`; it must cover the topics in `BLIND_SPOT_TOPICS`.

The README's checked boxes: every `[x]` line has a `· evidence:` part. Each relative markdown
link in it resolves to a file, and each `tests/...py::name` reference (or the `::name` shorthand
after one) names a function or class that is defined in that file.

Parts that wait on notes not written yet are `xfail(strict=False)`, reason "written in wave 2/3":
they pass as XPASS once the note lands, and the marker should go then. The checkers themselves are
tested on small synthetic inputs, so a vacuous pass is not mistaken for a working check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
REPO = POC.parents[1]
NOTES = POC / "notes"
README = POC / "README.md"
OVERHEAD = NOTES / "2026-10-09-gvisor-overhead.md"
BLIND_SPOTS = NOTES / "2026-10-02-blind-spots.md"
REMOTE_ENGINES = ("echo-python", "code-runner")
RUNTIMES = ("gvisor", "runc")
LATER = "written in wave 2/3"
EVIDENCE = "· evidence:"
NUMBER = re.compile(r"\d+(?:\.\d+)?")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
TEST_REF = re.compile(
    r"(?:(?P<file>(?:[\w.-]+/)*tests/[\w/.-]+\.py)|(?<=[`\s,]))::(?P<name>[A-Za-z_]\w*)"
)
# topic -> a pattern any one line of the note must match (case-insensitive).
BLIND_SPOT_TOPICS = {
    "per-call sandbox": r"per-call",
    "SandboxClaim": r"SandboxClaim",
    "/dev/shm": r"/dev/shm",
    "H01": r"\bH01\b",
    "Envoy": r"\bEnvoy\b",
    "claim deletion": r"claim.{0,60}(?:delete|deletion)|(?:delete|deletion).{0,60}claim",
}


def tables(text: str) -> list[list[list[str]]]:
    """Every markdown table in `text`: rows of stripped cells, the header first, no rule row."""
    found: list[list[list[str]]] = []
    rows: list[list[str]] = []
    for line in [*text.splitlines(), ""]:
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                rows.append(cells)
        elif rows:
            found.append(rows)
            rows = []
    return found


def has_number(row: list[str], cols: list[int]) -> bool:
    return any(i < len(row) and NUMBER.search(row[i]) for i in cols)


def measured_rows(text: str, engine: str, runtime: str) -> list[list[str]]:
    """Rows naming `engine` and `runtime` that hold a number under an `ms` header and a number under
    a `MiB` header."""
    hits = []
    for header, *rows in tables(text):
        ms_cols = [i for i, h in enumerate(header) if re.search(r"\bms\b", h)]
        mib_cols = [i for i, h in enumerate(header) if "MiB" in h]
        for row in rows:
            words = " ".join(row).lower()
            if engine not in words or not re.search(rf"\b{runtime}\b", words):
                continue
            if has_number(row, ms_cols) and has_number(row, mib_cols):
                hits.append(row)
    return hits


def names_exception(text: str, engine: str) -> bool:
    """A paragraph names `engine` and calls it an exception (or says it is not measured)."""
    return any(
        engine.lower() in p.lower() and re.search(r"exception|not measured", p, re.I)
        for p in re.split(r"\n\s*\n", text)
    )


def defines(path: Path, name: str) -> bool:
    return (
        re.search(rf"^\s*(?:async\s+def|def|class)\s+{name}\b", path.read_text(), re.M) is not None
    )


def evidence_problems(line: str, base: Path, repo: Path) -> list[str]:
    """What is wrong with one `[x]` line's evidence; empty when it all resolves."""
    if EVIDENCE not in line:
        return [f"no '{EVIDENCE}' part"]
    part = line.split(EVIDENCE, 1)[1]
    problems = []
    for target in LINK.findall(part):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        if not (base / target.split("#", 1)[0]).exists():
            problems.append(f"link {target} does not resolve")
    last: Path | None = None
    for ref in TEST_REF.finditer(part):
        if ref["file"]:
            root = base if ref["file"].startswith("tests/") else repo
            last = root / ref["file"]
            if not last.is_file():
                problems.append(f"{ref['file']} does not exist")
                last = None
                continue
        if last is None:
            problems.append(f"::{ref['name']} has no file before it")
        elif not defines(last, ref["name"]):
            problems.append(f"{last.name}::{ref['name']} is not defined")
    return problems


def checked_lines() -> list[str]:
    return [line for line in README.read_text().splitlines() if line.lstrip().startswith("- [x]")]


# --- exit criterion 9: the gVisor overhead note ---


def test_the_gvisor_overhead_note_exists() -> None:
    """Criterion 9: the overhead is recorded in its own dated note."""
    assert OVERHEAD.is_file(), OVERHEAD


@pytest.mark.parametrize("runtime", RUNTIMES)
@pytest.mark.parametrize("engine", REMOTE_ENGINES)
def test_the_overhead_note_measures_each_remote_engine(engine: str, runtime: str) -> None:
    """Criterion 9: each remote engine has a row per runtime with a latency (ms) and a memory
    (MiB) number, so the gVisor overhead can be read against runc."""
    rows = measured_rows(OVERHEAD.read_text(), engine, runtime)
    assert rows, f"no {engine} / {runtime} row with ms and MiB numbers in {OVERHEAD.name}"


def test_the_overhead_note_lists_typescript_as_an_exception() -> None:
    """Criterion 9, "or the exceptions are listed": echo-typescript cannot be a remote yet."""
    assert names_exception(OVERHEAD.read_text(), "typescript")


# --- exit criterion 10: the blind-spots note ---


def test_the_blind_spots_note_exists() -> None:
    """Criterion 10: the list of what the chassis cannot see in the remote lane exists."""
    assert BLIND_SPOTS.is_file(), BLIND_SPOTS


@pytest.mark.parametrize("topic", BLIND_SPOT_TOPICS)
def test_the_blind_spots_note_covers(topic: str) -> None:
    """Criterion 10: the note covers each topic the cluster work raised."""
    text = BLIND_SPOTS.read_text()
    assert re.search(BLIND_SPOT_TOPICS[topic], text, re.I), f"{topic!r} not in {BLIND_SPOTS.name}"


# --- the README's checked boxes ---


def test_every_checked_box_has_evidence_that_resolves() -> None:
    """Every `[x]` line has a `· evidence:` part whose links and test refs resolve. With no box
    checked this passes vacuously; the next test is the guard for that."""
    problems = {
        line[:70]: found
        for line in checked_lines()
        if (found := evidence_problems(line, POC, REPO))
    }
    assert not problems, problems


@pytest.mark.xfail(strict=False, reason=f"{LATER}: the close checks the boxes")
def test_the_readme_has_checked_boxes() -> None:
    """The evidence check above has something to check: at least one exit box is `[x]`."""
    assert checked_lines()


# --- the checkers, on synthetic input ---

GOOD_TABLE = """
| Engine | Runtime | Kernel | Ready ms | Request ms p50 / p95 | current MiB | working set MiB |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| echo-python | runc | 6.10 | 1200 | 2.1 / 3.0 | 60 | 55 |
| echo-python | gvisor | 4.4.0 | 2400 | 3.2 / 5.1 | 110 | 108 |

echo-typescript is not measured: an exception, it has no inbound token check.
"""


def test_measured_rows_finds_a_row_with_ms_and_mib_numbers() -> None:
    assert measured_rows(GOOD_TABLE, "echo-python", "gvisor")
    assert measured_rows(GOOD_TABLE, "echo-python", "runc")


def test_measured_rows_refuses_a_missing_engine_or_a_row_without_memory() -> None:
    assert not measured_rows(GOOD_TABLE, "code-runner", "gvisor")
    no_memory = GOOD_TABLE.replace("| 110 | 108 |", "| ? | ? |")
    assert not measured_rows(no_memory, "echo-python", "gvisor")
    assert measured_rows(no_memory, "echo-python", "runc")


def test_names_exception_needs_the_engine_and_the_word_in_one_paragraph() -> None:
    assert names_exception(GOOD_TABLE, "typescript")
    assert not names_exception("echo-typescript runs.\n\nOne exception: none.", "typescript")


def test_evidence_problems_accepts_a_line_that_resolves(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        "def test_one():\n    pass\nasync def test_two():\n"
    )
    (tmp_path / "note.md").write_text("x")
    line = (
        "- [x] Thing. · evidence: [note](note.md#part), `tests/test_a.py::test_one[p]`, "
        "`::test_two`, [web](https://example.com)"
    )
    assert evidence_problems(line, tmp_path, tmp_path) == []


def test_evidence_problems_flags_each_kind_of_gap(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_one():\n    pass\n")
    assert evidence_problems("- [x] Thing.", tmp_path, tmp_path) == [f"no '{EVIDENCE}' part"]
    line = (
        "- [x] Thing. · evidence: [gone](notes/gone.md), `tests/test_a.py::test_missing`, "
        "`tests/test_b.py::test_one`, `::test_orphan`"
    )
    assert evidence_problems(line, tmp_path, tmp_path) == [
        "link notes/gone.md does not resolve",
        "test_a.py::test_missing is not defined",
        "tests/test_b.py does not exist",
        "::test_orphan has no file before it",
    ]
