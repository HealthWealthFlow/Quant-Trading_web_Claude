import json

from qsd.cli import main


def test_status_command(capsys):
    assert main(["status"]) == 0
    assert "Next:" in capsys.readouterr().out


def test_config_command(capsys):
    assert main(["config"]) == 0
    assert "budgets" in json.loads(capsys.readouterr().out)


def test_db_init_and_info(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert main(["db", "--db", db, "init"]) == 0
    assert main(["db", "--db", db, "info"]) == 0
    out = capsys.readouterr().out
    assert "schema v3" in out and "ideas" in out
