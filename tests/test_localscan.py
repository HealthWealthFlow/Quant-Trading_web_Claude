import os

from fixtures import make_pdf
from sqlalchemy import select

from qsd.db import init_db, make_engine, session_scope
from qsd.db.models import LocalFile
from qsd.localscan import scan_paths


def test_scan_indexes_supported_files_read_only(tmp_path):
    root = tmp_path / "research"
    (root / "sub").mkdir(parents=True)
    (root / "a.pdf").write_bytes(make_pdf(["x"]))
    (root / "sub" / "b.md").write_text("# note", encoding="utf-8")
    (root / "ignore.exe").write_bytes(b"MZ")
    (root / ".git").mkdir()
    (root / ".git" / "c.md").write_text("skip", encoding="utf-8")
    snapshot = {p: p.stat().st_mtime for p in root.rglob("*")}

    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    r1 = scan_paths([root], engine)
    assert (r1.added, r1.skipped) == (2, 1)
    assert {p: p.stat().st_mtime for p in root.rglob("*")} == snapshot  # nothing touched

    r2 = scan_paths([root], engine)
    assert (r2.added, r2.unchanged) == (0, 2)

    md = root / "sub" / "b.md"
    md.write_text("# changed", encoding="utf-8")
    os.utime(md, (md.stat().st_atime, md.stat().st_mtime + 10))
    r3 = scan_paths([root], engine)
    assert r3.updated == 1
    with session_scope(engine) as s:
        formats = sorted(f.format for f in s.scalars(select(LocalFile)))
    assert formats == ["md", "pdf"]


def test_missing_folder_counts_as_error(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    assert scan_paths([tmp_path / "nope"], engine).errors == 1
