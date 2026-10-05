import json

from qsd.cli import main
from qsd.db.models import SCHEMA_VERSION


def test_status_command(capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Next:" in out or "All milestones done." in out


def test_config_command(capsys):
    assert main(["config"]) == 0
    assert "budgets" in json.loads(capsys.readouterr().out)


def test_db_init_and_info(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert main(["db", "--db", db, "init"]) == 0
    assert main(["db", "--db", db, "info"]) == 0
    out = capsys.readouterr().out
    assert f"schema v{SCHEMA_VERSION}" in out and "ideas" in out


def test_reground_with_nothing_stored(tmp_path, capsys):
    assert main(["reground", "--db", str(tmp_path / "r.sqlite")]) == 0
    assert "No stored AI extractions" in capsys.readouterr().out


def test_reground_cli_rereads_local_file_and_rescores(tmp_path, capsys):
    from fixtures import make_pdf
    from test_ai import GOOD_B, PAGES, STAGE_A, FakeProvider, settings

    from qsd.ai import AIGateway, extract_ideas
    from qsd.db import init_db, make_engine, session_scope
    from qsd.db.models import Source
    from qsd.handlers import parse_file

    pdf = tmp_path / "tsmom.pdf"
    pdf.write_bytes(make_pdf(PAGES))
    db = str(tmp_path / "r.sqlite")
    engine = make_engine(db)
    init_db(engine)
    result = parse_file(pdf)
    with session_scope(engine) as s:
        src = Source(title="TSMOM", local_path=str(pdf), content_hash=result.sha256)
        s.add(src)
        s.flush()
        sid = src.id
    extract_ideas(AIGateway(engine, settings(), {"fake": FakeProvider([STAGE_A, GOOD_B])}), engine, settings(),
                  sid, result)
    assert main(["reground", "--db", db]) == 0
    out = capsys.readouterr().out
    assert f"source {sid}: idea 1  DISCOVERED -> DISCOVERED  removed values 1 -> 1" in out
    assert "quotes realigned: rebalance" in out and "Re-scored (no AI calls were made)" in out

    assert main(["factcheck", "1", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "== stop_rule: exit after 10% loss" in out and "AI quote:      stop loss of 10%" in out
    assert "exact match:   no" in out and "closest text:" in out

    pdf.write_bytes(make_pdf(PAGES[:2]))  # document changed → refuse to re-ground against different text
    assert main(["reground", "--db", db]) == 0
    assert "document changed since extraction" in capsys.readouterr().out
