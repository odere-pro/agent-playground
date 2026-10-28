"""`python -m chassis_contracts.redteam [--write PATH]`: run the red-team set on both configs,
print the pass rate per category, and write the results JSON when `--write` is given.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from chassis_contracts.redteam import CATEGORIES, CONFIGS, dump, results, run_all


async def _main() -> dict[str, Any]:
    outcomes = []
    for config in CONFIGS:
        outcomes.extend(await run_all(config))
    data = results(outcomes)
    for config, row in data["summary"].items():
        print(f"{config}:")
        for category in CATEGORIES:
            cell = row["categories"][category]
            print(f"  {category:24} {cell['stopped']}/{cell['cases']}")
        total = row["total"]
        print(f"  {'total':24} {total['stopped']}/{total['cases']}  missed: {row['missed']}")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="PoC-7 red-team set through the chassis")
    parser.add_argument("--write", type=Path, default=None, help="where to write the results")
    args = parser.parse_args()
    data = asyncio.run(_main())
    if args.write is not None:
        args.write.write_text(dump(data))
        print(f"wrote {args.write}")


if __name__ == "__main__":
    main()
