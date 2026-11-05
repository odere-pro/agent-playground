"""The PoC-4 load matrix: an entry script kept so the dated commands in `notes/` still run.

The code is `load_tools.run_matrix` (`packages/load-tools`); run `make load-test` or
`uv run python -m load_tools.run_matrix`. The flags, defaults, and outputs are the same.
"""

from __future__ import annotations

import sys

from load_tools.run_matrix import main

if __name__ == "__main__":
    sys.exit(main())
