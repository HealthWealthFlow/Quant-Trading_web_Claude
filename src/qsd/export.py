"""Markdown notes per strategy idea (e.g. into an Obsidian vault) — idea adapted from the user's Idea Extractor.

One note per idea: `QSD-000123 <strategy name>.md` in `<notes_dir>/<notes_subfolder>`. Safety rules:
- A file is only (over)written when its front matter says `qsd_id: <this idea>`; any other file is left untouched.
- Text from sources is untrusted: Markdown/HTML characters are escaped so a paper can't inject links, embeds or
  formatting into the vault; only http(s) source links are written as links.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, select

from .config import Settings
from .db import session_scope
from .db.models import Idea
from .packaging import build_package
from .taxonomy import REGIME_LABELS, MarketRegime

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>!|#~=$^])")
_ID_LINE = re.compile(r"^qsd_id:\s*(\d+)\s*$", re.M)
DASHBOARD = "http://127.0.0.1:8877"


@dataclass
class ExportReport:
    folder: Path
    written: int = 0
    skipped: int = 0
    files: list[Path] = field(default_factory=list)
    skipped_files: list[Path] = field(default_factory=list)


def md(text) -> str:
    """Untrusted text as inert Markdown: one line, special characters escaped."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(text if text is not None else "").split()))


def _y(value) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON strings/lists are valid YAML


def _safe_name(name: str) -> str:
    cleaned = _ILLEGAL.sub(" ", name)
    return " ".join(cleaned.split()).strip(" .")[:80] or "untitled"


def _link(url: str | None) -> str | None:
    if not url or not re.match(r"https?://", url, re.I):
        return None
    return re.sub(r"[\s()<>\[\]]", lambda m: f"%{ord(m.group(0)):02X}", url)


def note_markdown(pkg) -> str:
    sc, prov = pkg.scores, pkg.provenance
    ps = prov.get("primary_source") or {}
    suited = [REGIME_LABELS[MarketRegime(r["regime"])] for r in pkg.market_regimes if r["suitability"] == "SUITED"]
    tags = ["qsd", "strategy-idea", pkg.maturity.lower(), *[a.lower() for a in pkg.asset_classes],
            *[r["regime"].lower() for r in pkg.market_regimes if r["suitability"] == "SUITED"]]
    front = [
        "---",
        f"title: {_y(pkg.strategy_name)}",
        f"qsd_id: {pkg.idea_id}",
        f"strategy_id: {_y(pkg.strategy_id)}",
        f"generated: {_y(pkg.generated_at[:10])}",
        f"status: {_y(pkg.status)}",
        f"maturity: {_y(pkg.maturity)}",
        f"completeness: {sc.get('formalization_completeness')}",
        f"quality: {sc.get('idea_quality_normalized')}",
        f"instruments: {_y(pkg.asset_classes)}",
        f"market_direction: {_y(suited)}",
        f"source: {_y(ps.get('canonical_url') or ps.get('url') or ps.get('local_path') or '')}",
        f"source_title: {_y(ps.get('title') or '')}",
        f"tags: {_y(sorted(set(tags)))}",
        "---",
    ]
    lines = [
        f"# {md(pkg.strategy_name)}", "",
        f"> [!warning] {md(pkg.warning)}", "",
        f"**Status** {md(pkg.status)} · **Maturity** {md(pkg.maturity)} · "
        f"**Rules complete** {sc.get('formalization_completeness') or '—'}% · "
        f"**Quality** {round(sc['idea_quality_normalized'], 1) if sc.get('idea_quality_normalized') else '—'} "
        f"(coverage {round((sc.get('coverage') or 0) * 100)}%)", "",
        "## Hypothesis", md(pkg.hypothesis), "",
        "## Source",
    ]
    link = _link(ps.get("canonical_url") or ps.get("url"))
    title = md(ps.get("title") or "unknown")
    lines.append(f"- [{title}]({link})" if link else f"- {title}" + (f" — {md(ps.get('local_path'))}"
                                                                     if ps.get("local_path") else ""))
    lines.append(f"- {md(ps.get('author') or 'unknown')} · {md(ps.get('publication_date') or 'unknown')} · "
                 f"tier {ps.get('tier') or 'unrated'}")
    lines += ["", "## Rules stated by the source", "| Rule | Value | Evidence |", "|---|---|---|"]
    for key, r in pkg.known_rules.items():
        where = f" ({md(r.get('location'))})" if r.get("location") else ""
        lines.append(f"| {md(key)} | {md(r.get('value'))} | “{md(r.get('quote'))}”{where} |")
    if not pkg.known_rules:
        lines.append("| — | none stated | |")
    lines += ["", "## Missing rules (UNKNOWN — not guessed)", ", ".join(md(u) for u in pkg.unknown_rules) or "none",
              "", "## Market direction", "| Direction | Suitability | Basis |", "|---|---|---|"]
    for r in pkg.market_regimes:
        lines.append(f"| {REGIME_LABELS[MarketRegime(r['regime'])]} | {md(r['suitability'])} | {md(r['basis'])} |")
    claims = pkg.source_claims.get("claims") or {}
    lines += ["", "## Source claims (NOT validated)"]
    lines += [f"- {md(k)}: {md(c.get('value'))}" for k, c in claims.items()] or ["- none"]
    flags = pkg.concerns.get("red_flags") or []
    lines += ["", "## Red flags"] + ([f"- {md(f)}" for f in flags] or ["- none detected"])
    removed = (pkg.grounding or {}).get("removed") or []
    if removed:
        lines += ["", f"## Fact-check: {len(removed)} value(s) removed",
                  "Values the AI offered that could not be found in the source; they are not facts."]
        lines += [f"- {md(r.get('field'))}: {md(r.get('value'))} ({md(r.get('reason'))})" for r in removed]
    blocking = pkg.handoff.get("blocking_reasons") or []
    lines += ["", "## Backtest handoff", "Eligible" if pkg.handoff.get("eligible") else
              "Not yet: " + "; ".join(md(b) for b in blocking), "",
              f"[Open in QSD dashboard]({DASHBOARD}/ideas/{pkg.idea_id})", ""]
    return "\n".join(front + [""] + lines)


def _target(folder: Path, idea_id: int, name: str) -> Path:
    existing = sorted(folder.glob(f"QSD-{idea_id:06d} *.md"))
    return existing[0] if existing else folder / f"QSD-{idea_id:06d} {_safe_name(name)}.md"


def _owned(path: Path, idea_id: int) -> bool:
    if not path.exists():
        return True
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return False
    m = _ID_LINE.search(head.split("\n---", 1)[0])
    return bool(m and int(m.group(1)) == idea_id)


def export_notes(engine: Engine, settings: Settings, *, campaign_id: int | None = None,
                 idea_ids: list[int] | None = None, notes_dir: Path | None = None,
                 group_by: str = "family") -> ExportReport:
    """Write one note per idea, optionally grouped into a subfolder.

    Measured need: a harvest produces ideas of several kinds at once, and a reader wants them separated rather
    than in one flat list. `group_by` is the strategy family (dominant first), the asset class, or nothing.
    """
    base = notes_dir or settings.export.notes_dir
    if base is None:
        raise ValueError("no notes folder: set export.notes_dir in config/local.yaml or pass --dir")
    folder = Path(base) / settings.export.notes_subfolder
    folder.mkdir(parents=True, exist_ok=True)
    with session_scope(engine) as s:
        q = select(Idea.id, Idea.strategy_name, Idea.strategy_families, Idea.asset_classes).order_by(Idea.id)
        if campaign_id is not None:
            q = q.where(Idea.campaign_id == campaign_id)
        if idea_ids:
            q = q.where(Idea.id.in_(idea_ids))
        rows = s.execute(q).all()
    rep = ExportReport(folder)
    for idea_id, name, families, assets in rows:
        target_dir = folder / _group_folder(group_by, families, assets)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = _target(target_dir, idea_id, name)
        if not _owned(path, idea_id):
            rep.skipped += 1
            rep.skipped_files.append(path)
            continue
        path.write_text(note_markdown(build_package(engine, settings, idea_id)), encoding="utf-8")
        rep.written += 1
        rep.files.append(path)
    return rep


def _group_folder(group_by: str, families: list | None, assets: list | None) -> Path:
    """The subfolder an idea belongs in. Never empty: ungrouped work still needs somewhere to live."""
    if group_by == "family":
        return Path(_safe_name(families[0]) if families else "Unclassified")
    if group_by == "asset":
        return Path(_safe_name(assets[0]) if assets else "Unclassified")
    return Path()
