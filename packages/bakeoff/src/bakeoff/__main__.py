"""`python -m bakeoff`: prints the usage."""

import sys

from bakeoff import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
