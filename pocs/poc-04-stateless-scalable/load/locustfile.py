"""The PoC-4 Locust user: an entry file kept so the dated `locust -f` commands still run.

The code is `packages/load-tools/src/load_tools/locustfile.py`. Locust runs in its
own venv (`load_tools.run_matrix.install_locust`), so the workspace is not installed and
`load_tools` cannot be imported.
This file loads the packaged file by path instead, and re-exports its user class, which is what
Locust looks for.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

PACKAGED = Path(__file__).resolve().parents[3] / "packages/load-tools/src/load_tools/locustfile.py"


def _load_packaged() -> ModuleType:
    spec = importlib.util.spec_from_file_location("load_tools_locustfile", PACKAGED)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {PACKAGED}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RunUser = _load_packaged().RunUser
