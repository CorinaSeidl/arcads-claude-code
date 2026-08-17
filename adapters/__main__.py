"""Terminal entry point: `python3 -m adapters list`.

Prints the status of every registered backend (capabilities, credential check,
whether it has a real executable path yet) without making any network calls. This is
the safe way to check "what can I actually generate with right now" before invoking a
skill. See docs/adapter/DESIGN.md and adapters/README.md.
"""

from __future__ import annotations

import sys

from adapters.registry import list_backends


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "list":
        print("usage: python3 -m adapters list", file=sys.stderr)
        return 2

    for entry in list_backends():
        flag = "✓" if entry["credentials_ok"] else "✗"
        real = "executable" if entry["executable"] else "stub — not wired yet"
        caps = ", ".join(entry["capabilities"]) or "(none declared)"
        print(f"{flag} {entry['name']:<10} [{real}]  capabilities: {caps}")
        print(f"    {entry['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
