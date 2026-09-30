"""Safe ZIP reading (spec §101): limits on member count, total size and compression ratio; no path traversal,
no absolute paths, no symlinks. Nothing is ever written to disk; members are read into memory on demand.

Office files (DOCX/PPTX/XLSX) and EPUB are ZIP containers, so they are checked here before any parser sees them.
"""

from __future__ import annotations

import io
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath


class UnsafeArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int = 2000
    max_total_uncompressed: int = 300 * 1024 * 1024
    max_member_uncompressed: int = 100 * 1024 * 1024
    max_ratio: float = 200.0  # uncompressed / compressed, per member


DEFAULT_LIMITS = ArchiveLimits()


def _check_name(name: str) -> None:
    if not name or "\x00" in name:
        raise UnsafeArchiveError("empty or NUL member name")
    normalized = name.replace("\\", "/")
    p = PurePosixPath(normalized)
    if p.is_absolute() or normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise UnsafeArchiveError(f"absolute path in archive: {name!r}")
    if ".." in p.parts:
        raise UnsafeArchiveError(f"path traversal in archive: {name!r}")


class SafeZip:
    def __init__(self, data: bytes, limits: ArchiveLimits = DEFAULT_LIMITS):
        try:
            self._zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise UnsafeArchiveError(f"not a valid zip: {e}") from e
        self.limits = limits
        infos = self._zf.infolist()
        if len(infos) > limits.max_members:
            raise UnsafeArchiveError(f"too many members ({len(infos)} > {limits.max_members})")
        total = 0
        for info in infos:
            _check_name(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UnsafeArchiveError(f"symlink in archive: {info.filename!r}")
            if info.file_size > limits.max_member_uncompressed:
                raise UnsafeArchiveError(f"member too large: {info.filename!r}")
            if info.compress_size and info.file_size / info.compress_size > limits.max_ratio:
                raise UnsafeArchiveError(f"suspicious compression ratio: {info.filename!r}")
            total += info.file_size
        if total > limits.max_total_uncompressed:
            raise UnsafeArchiveError("archive expands beyond the size limit")
        self.infos = [i for i in infos if not i.is_dir()]

    def names(self) -> list[str]:
        return [i.filename for i in self.infos]

    def read(self, name: str) -> bytes:
        info = self._zf.getinfo(name)
        # Enforce the declared size while reading, in case headers lie.
        with self._zf.open(info) as fh:
            data = fh.read(self.limits.max_member_uncompressed + 1)
        if len(data) > self.limits.max_member_uncompressed:
            raise UnsafeArchiveError(f"member exceeds size limit while reading: {name!r}")
        return data


def assert_safe_zip(data: bytes, limits: ArchiveLimits = DEFAULT_LIMITS) -> SafeZip:
    return SafeZip(data, limits)
