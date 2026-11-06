"""The steady no-retry client: an entry script kept so the dated commands in `notes/` still run.

The code is `load_tools.steady_client` (`packages/load-tools`); run
`uv run python -m load_tools.steady_client`. The flags, defaults, and outputs are the same.
"""

from __future__ import annotations

import sys

from load_tools.steady_client import main

if __name__ == "__main__":
    sys.exit(main())
