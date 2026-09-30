"""Read-only local folder indexing (spec §111, §112).

Files are only opened for reading and hashing; nothing in the user's folders is created, moved or modified.
Unchanged files (same size + mtime) are not re-hashed.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, select

from .db import session_scope
from .db.models import LocalFile
from .handlers import EXTENSION_FORMATS

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "$RECYCLE.BIN", "System Volume Information"}


@dataclass
class ScanReport:
    seen: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0


def _hash_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def iter_candidate_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.is_symlink():
                continue
            yield p


def scan_paths(roots: list[Path], engine: Engine, max_file_mb: int = 200) -> ScanReport:
    report = ScanReport()
    max_bytes = max_file_mb * 1024 * 1024
    with session_scope(engine) as s:
        for root in roots:
            if not root.exists():
                report.errors += 1
                continue
            files = [root] if root.is_file() else iter_candidate_files(root)
            for path in files:
                report.seen += 1
                fmt = EXTENSION_FORMATS.get(path.suffix.lower())
                try:
                    st = path.stat()
                except OSError:
                    report.errors += 1
                    continue
                if fmt is None or st.st_size > max_bytes:
                    report.skipped += 1
                    continue
                key = str(path.resolve())
                row = s.scalars(select(LocalFile).where(LocalFile.path == key)).one_or_none()
                if row and row.size == st.st_size and row.mtime == st.st_mtime:
                    report.unchanged += 1
                    continue
                try:
                    digest = _hash_file(path)
                except OSError:
                    report.errors += 1
                    continue
                if row is None:
                    s.add(LocalFile(path=key, content_hash=digest, format=fmt, size=st.st_size, mtime=st.st_mtime))
                    report.added += 1
                else:
                    changed = row.content_hash != digest
                    row.content_hash, row.size, row.mtime = digest, st.st_size, st.st_mtime
                    if changed:
                        row.processed_status = "PENDING"
                    report.updated += 1
    return report
