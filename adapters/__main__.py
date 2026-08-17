"""`python3 -m adapters <command>` — see adapters/cli.py for the command implementations."""

from __future__ import annotations

import sys

from adapters.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
