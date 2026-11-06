"""The bake-off kit holds no eval code (R6-14): the eval trial, gate, and red team are `eval_kit`.

The compatibility modules (`COMPAT`) keep `python -m bakeoff.<old>` in dated notes running. Each
one only runs `eval_kit.<old>.main` when it is `__main__`, so importing it loads no eval module.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from bakeoff.envs import build_env
from bakeoff.registry import ROOT

SRC = ROOT / "packages/bakeoff/src/bakeoff"
EVAL_MODULES = (
    "dialects",
    "evalcheck",
    "evalgate",
    "evalreport",
    "evalrun",
    "evaltrial",
    "golden",
    "langfuse_datasets",
    "redteam",
)
COMPAT = {
    "evalgate.py": "eval_kit.evalgate",
    "evalrun.py": "eval_kit.evalrun",
    "evaltrial.py": "eval_kit.evaltrial",
    "golden/generate.py": "eval_kit.golden.generate",
    "langfuse_datasets.py": "eval_kit.langfuse_datasets",
    "redteam.py": "eval_kit.redteam",
}
_IMPORT_ALL = """
import importlib, pkgutil, sys
import bakeoff
names = [i.name for i in pkgutil.walk_packages(bakeoff.__path__, "bakeoff.")]
for name in names:
    if name != "bakeoff.__main__":
        importlib.import_module(name)
print(sorted(m for m in sys.modules if m.split(".")[0] == "eval_kit"))
"""


def _is_eval(module: str) -> bool:
    top, _, rest = module.partition(".")
    return top == "eval_kit" or (top == "bakeoff" and rest.split(".")[0] in EVAL_MODULES)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    return names + [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]


def test_bakeoff_source_imports_no_eval_module() -> None:
    """No `import` in the bake-off kit names `eval_kit` or a moved eval module."""
    files = sorted(SRC.rglob("*.py"))

    found = {str(f.relative_to(SRC)): m for f in files for m in _imports(f) if _is_eval(m)}

    assert files
    assert found == {}


def test_bakeoff_compat_modules_are_the_moved_clis() -> None:
    """Only the moved CLIs keep a module in `bakeoff`, and each runs the `eval_kit` one."""
    left = {str(f.relative_to(SRC)) for f in SRC.rglob("*.py")}
    texts = {name: (SRC / name).read_text(encoding="utf-8") for name in COMPAT}

    assert left >= set(COMPAT)
    assert {name: f'"{target}"' in texts[name] for name, target in COMPAT.items()} == dict.fromkeys(
        COMPAT, True
    )


@pytest.mark.slow
def test_bakeoff_importing_every_module_loads_no_eval_module(tmp_path: Path) -> None:
    """Importing every `bakeoff` module, the compatibility ones too, loads no `eval_kit` module."""
    env = build_env(tmp_path)

    out = subprocess.run(
        [sys.executable, "-c", _IMPORT_ALL], env=env, capture_output=True, text=True, check=True
    )

    assert out.stdout.strip() == "[]"
