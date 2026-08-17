"""Atomic text file writes — temp file + os.replace().

Used for provenance.json and approval-manifest.json so a crash or interruption mid-
write can never leave a partially-written file, and a *new* write can never leave the
*old* content in place if it fails partway — the target path either gets the complete
new content, or is left completely untouched. See docs/adapter/DESIGN.md (Phase 3
review-fix addendum, B2).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` atomically.

    Writes to a temp file in the same directory as `path` (so the final rename is on
    the same filesystem, which is what makes it atomic), fsyncs it, then
    ``os.replace()``s it onto the target. On any failure, the temp file is removed and
    `path` is left exactly as it was before the call — never a partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # atomic rename on POSIX (same filesystem)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
