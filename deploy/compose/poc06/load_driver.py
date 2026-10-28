"""The PoC-4 load matrix on the scale stack, through scale-slm.sh (PoC-6, scripts/poc06_mac.sh).

    python load_driver.py --out DIR [run_matrix.py arguments]

`run_matrix.py` is the PoC-4 file, unchanged. This driver loads it, points it at scale-slm.sh
(so echo-openai-agents and, with POC06_SCALE_MODEL=slm, the llama-server behind LiteLLM work),
adds echo-openai-agents to its engine list, and sends its raw files and `results.json` to DIR
instead of the PoC-4 notes. It then writes DIR/results.md: one row per engine and pair count.
Nothing here reads or prints a key.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MATRIX = ROOT / "pocs" / "poc-04-stateless-scalable" / "load" / "run_matrix.py"
EXTRA_ENGINES = ("echo-openai-agents",)


def load_matrix() -> ModuleType:
    spec = importlib.util.spec_from_file_location("poc04_run_matrix", MATRIX)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MATRIX}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up here
    spec.loader.exec_module(module)
    return module


def render_markdown(results: dict[str, object], out: Path) -> str:
    lines = [
        "# PoC-6 load results",
        "",
        "| Engine | Pairs | Users | RPS | OK RPS | p50 ms | p95 ms | Failures / requests |",
        "| ------ | ----- | ----- | --- | ------ | ------ | ------ | ------------------- |",
    ]
    engines = results.get("engines", {})
    assert isinstance(engines, dict)
    for engine, by_pairs in sorted(engines.items()):
        for pairs, row in sorted(by_pairs.items(), key=lambda item: int(item[0])):
            lines.append(
                f"| {engine} | {pairs} | {row['users']} | {row['rps']} | {row['ok_rps']} "
                f"| {row['p50_ms']} | {row['p95_ms']} | {row['failures']} / {row['requests']} |"
            )
    lines += ["", f"Raw: `{out.name}/raw/` (Locust CSVs, docker stats), `results.json`.", ""]
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    if len(args) < 2 or args[0] != "--out":
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(args[1]).resolve()
    rest = args[2:]
    matrix = load_matrix()
    matrix.SCALE_SH = HERE / "scale-slm.sh"
    matrix.ENGINES = (*matrix.ENGINES, *EXTRA_ENGINES)
    matrix.NOTES = out
    if "--results" not in rest:
        rest += ["--results", str(out / "results.json")]
    code: int = matrix.main(rest)
    results_path = out / "results.json"
    if results_path.is_file() and "--dry-run" not in rest:
        (out / "results.md").write_text(
            render_markdown(json.loads(results_path.read_text(encoding="utf-8")), out),
            encoding="utf-8",
        )
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
