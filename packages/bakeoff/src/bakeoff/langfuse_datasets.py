"""Moved to `eval_kit.langfuse_datasets` (R6-14).

Kept so `python -m bakeoff.langfuse_datasets` in dated notes still runs.
Importing it loads nothing. Run as `__main__`, it runs `eval_kit.langfuse_datasets.main`.
"""

import importlib
import sys

if __name__ == "__main__":
    sys.exit(importlib.import_module("eval_kit.langfuse_datasets").main())
