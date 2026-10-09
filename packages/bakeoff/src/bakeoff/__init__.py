"""bakeoff: the PoC-6 benchmark kit. A skeleton today; later tasks add the runner and scorecard."""

USAGE = """usage: python -m bakeoff [ARGS]

The PoC-6 benchmark kit. It is a skeleton: no command exists yet.
Run it with `make bakeoff ARGS="..."`.
"""

__all__ = ["USAGE", "main"]


def main(argv: list[str] | None = None) -> int:
    """Print the usage and return 2: there is nothing to run yet."""
    print(USAGE, end="")
    return 2
