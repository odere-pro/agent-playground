"""Moved to `eval_kit.redteam` (R6-14).

Kept so `python -m bakeoff.redteam` in dated notes still runs.
Importing it loads nothing. Run as `__main__`, it runs `eval_kit.redteam.main`.
"""

import importlib
import sys

if __name__ == "__main__":
    sys.exit(importlib.import_module("eval_kit.redteam").main())
