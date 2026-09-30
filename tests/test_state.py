import pytest

from qsd.state import STATE_FILE, load_state, next_milestone, set_status, validate_state


def test_repo_state_file_is_valid():
    state = load_state(STATE_FILE)
    assert validate_state(state) == []
    assert next_milestone(state) is not None


def _state():
    return {"milestones": [
        {"id": "M0", "title": "a", "status": "done"},
        {"id": "M1", "title": "b", "status": "todo"},
    ]}


def test_next_milestone_skips_done():
    assert next_milestone(_state())["id"] == "M1"


def test_validate_detects_problems():
    s = _state()
    s["milestones"][0]["status"] = "bogus"
    s["milestones"].append({"id": "M1", "title": "dup", "status": "todo"})
    errors = validate_state(s)
    assert any("invalid status" in e for e in errors)
    assert any("duplicate" in e for e in errors)


def test_set_status():
    s = _state()
    set_status(s, "M1", "in_progress", note="started")
    assert s["milestones"][1]["status"] == "in_progress"
    with pytest.raises(ValueError):
        set_status(s, "M1", "nope")
    with pytest.raises(KeyError):
        set_status(s, "M9", "done")
