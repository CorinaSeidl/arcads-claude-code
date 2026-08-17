"""Regression guard (review-fix N2): running the test suite must never modify the
committed, production logs/adapter-calls.jsonl.

Rather than trusting that every individual test remembers to mock _ADAPTER_LOG (an
earlier version of this suite got that wrong — see the Phase 3 review), this spawns
the full suite as a clean child process (offline: `python3 -m unittest`, no network,
no vendor call) and diffs the committed log's hash before/after. A guard env var
prevents this test from recursively re-running itself inside the nested invocation.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ADAPTER_LOG = _REPO_ROOT / "logs" / "adapter-calls.jsonl"
_GUARD_ENV_VAR = "ADAPTERS_TEST_LOG_HYGIENE_NESTED"


def _hash() -> str | None:
    if not _ADAPTER_LOG.exists():
        return None
    return hashlib.sha256(_ADAPTER_LOG.read_bytes()).hexdigest()


class TestSuiteDoesNotTouchCommittedLog(unittest.TestCase):
    @unittest.skipIf(
        os.environ.get(_GUARD_ENV_VAR) == "1",
        "nested invocation — avoid recursively re-running this guard inside itself",
    )
    def test_full_suite_leaves_committed_log_untouched(self) -> None:
        before = _hash()
        env = dict(os.environ)
        env[_GUARD_ENV_VAR] = "1"
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "adapters/tests"],
            cwd=str(_REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        after = _hash()
        self.assertEqual(
            proc.returncode,
            0,
            f"nested suite run failed (exit {proc.returncode}):\n"
            f"stdout(tail):\n{proc.stdout[-2000:]}\nstderr(tail):\n{proc.stderr[-3000:]}",
        )
        self.assertEqual(
            before,
            after,
            "the test suite modified the committed logs/adapter-calls.jsonl — some "
            "test is writing to the real provenance log instead of a mocked path.\n"
            f"stderr(tail):\n{proc.stderr[-2000:]}",
        )


if __name__ == "__main__":
    unittest.main()
