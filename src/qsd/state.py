"""Build-progress state (PROJECT_STATE.json) so any session can resume from the next incomplete milestone."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import REPO_ROOT

STATE_FILE = REPO_ROOT / "PROJECT_STATE.json"
VALID_STATUS = {"todo", "in_progress", "done", "blocked"}


def load_state(path: Path = STATE_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any], path: Path = STATE_FILE) -> None:
    state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_state(state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    milestones = state.get("milestones")
    if not isinstance(milestones, list) or not milestones:
        return ["milestones must be a non-empty list"]
    ids = [m.get("id") for m in milestones]
    if len(ids) != len(set(ids)):
        errors.append("duplicate milestone ids")
    for m in milestones:
        if m.get("status") not in VALID_STATUS:
            errors.append(f"{m.get('id')}: invalid status {m.get('status')!r}")
    if sum(1 for m in milestones if m.get("status") == "in_progress") > 1:
        errors.append("more than one milestone in_progress")
    return errors


def next_milestone(state: dict[str, Any]) -> dict[str, Any] | None:
    for m in state["milestones"]:
        if m["status"] in ("in_progress", "todo", "blocked"):
            return m
    return None


def set_status(state: dict[str, Any], milestone_id: str, status: str, note: str | None = None) -> None:
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status {status!r}")
    for m in state["milestones"]:
        if m["id"] == milestone_id:
            m["status"] = status
            if note:
                m.setdefault("notes", []).append(note)
            return
    raise KeyError(milestone_id)
