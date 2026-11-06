"""Moved to `eval_kit.evalrun` (R6-14).

Kept so `python -m bakeoff.evalrun` in dated notes still runs.
Importing it loads nothing. Run as `__main__`, it runs `eval_kit.evalrun.main`.
"""

import importlib
import sys

if __name__ == "__main__":
    sys.exit(importlib.import_module("eval_kit.evalrun").main())
