import json

from qsd.cli import main


def test_status_command(capsys):
    assert main(["status"]) == 0
    assert "Next:" in capsys.readouterr().out


def test_config_command(capsys):
    assert main(["config"]) == 0
    assert "budgets" in json.loads(capsys.readouterr().out)
