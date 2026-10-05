"""Bridge QSD's ideas to a downstream backtester (Quant Auto OS) as a portable bundle.

QSD finds and grounds strategy hypotheses; the backtester turns one into a runnable spec, backtests it and
optimizes it. The two are separate systems with separate databases, so the handover is a *bundle of files*
rather than a write into the other system's tables: nothing here opens another project's database, and the
other side imports through its own API (`app.research.inbox.register_source` / `create_idea`), which keeps its
dedupe, status history and - most importantly - its parameter provenance intact.

What crosses the boundary, and why:

* the source, registered with its real URL, author, date and tier, so an idea in the other system is as
  attributable as it is here;
* the idea's rules in the source's own words, each with the verbatim quote it was grounded against, so a value
  can be recorded there as `SOURCE` rather than as a model's suggestion;
* the untouched document text, so the other system can re-read the paper itself instead of trusting this
  summary;
* the origin of every rule (stated by the source, or proposed by us), because a backtest is only a test of the
  source's hypothesis to the extent that the source stated it.

Nothing in this module decides whether a strategy works, and it never marks anything validated.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, select

from ..config import Settings
from ..db import session_scope
from ..db.models import Idea, Source, SourceFact
from ..taxonomy import UNKNOWN
from .package import build_package

BUNDLE_VERSION = "qsd-bridge-1"

#: QSD's source tier is already the downstream vocabulary (1 = peer-reviewed, 4 = promotional), so it crosses
#: unchanged rather than being re-guessed.
_KIND_BY_TIER = {1: "paper", 2: "paper", 3: "code", 4: "web"}

#: Rules that constitute the decision. Everything else is how the decision is implemented. `algorithm_rule`
#: belongs here for the same reason D38 added it: a portfolio-weight or model-driven strategy states its
#: decision as an update equation rather than a bar rule, and omitting it made idea 31 look like it had no
#: decision at all.
_DECISION_RULES = ("entry_rule", "algorithm_rule", "signal", "exit_rule", "stop_rule", "take_profit_rule")


def _location(fact: SourceFact) -> str:
    if fact.page:
        return f"p.{fact.page}"
    if fact.slide:
        return f"slide {fact.slide}"
    if fact.timestamp_start:
        return fact.timestamp_start
    return fact.section or fact.location or ""


def _quote_for(facts: dict[str, SourceFact], field: str) -> dict | None:
    f = facts.get(field.upper())
    if f is None or not f.quote:
        return None
    return {"quote": f.quote, "location": _location(f)}


def _evidence_digest(src: Source, facts: list[SourceFact]) -> str:
    """A citable record of every quote QSD extracted from this source, when the document text is gone.

    The HTTP cache is bounded and keyed by URL, so an older document's bytes may no longer be on disk even
    though the quotes taken from it are. Rather than export nothing, this is what QSD actually has: the source
    identity plus each verbatim quote with the page or timestamp it came from. It is labelled as a digest, not
    as the paper, so the downstream system can re-read it to attribute a value and still knows to fetch the
    original for anything more.
    """
    lines = [f"# Evidence digest: {src.title or f'source {src.id}'}",
             "",
             "This is NOT the source document. It is the record of every quoted passage QSD extracted from it, "
             "grounded verbatim against the original text, with the location each came from.",
             "",
             f"- original: {src.canonical_url or src.url or src.local_path or 'unknown'}",
             f"- QSD source id: {src.id}",
             f"- content hash of the document QSD read: {src.content_hash or 'unknown'}",
             f"- tier: {src.tier}",
             ""]
    for f in sorted(facts, key=lambda x: (x.idea_id or 0, x.fact_type)):
        if not f.quote:
            continue
        lines.append(f"## {f.fact_type} (idea {f.idea_id})")
        if _location(f):
            lines.append(f"location: {_location(f)}")
        lines.append("")
        lines.append(f"> {f.quote}")
        lines.append("")
    return "\n".join(lines)


def _rule_rows(pkg, facts: dict[str, SourceFact]) -> list[dict]:
    """Every stated rule with the source's own words behind it, in the shape the importer can attribute."""
    rows = []
    for field, ev in (pkg.known_rules or {}).items():
        value = getattr(ev, "value", None) if not isinstance(ev, dict) else ev.get("value")
        if not value or value == UNKNOWN:
            continue
        quote = getattr(ev, "quote", None) if not isinstance(ev, dict) else ev.get("quote")
        rows.append({"field": field, "value": value, "quote": quote,
                     "origin": "SOURCE" if quote else "UNRESOLVED"})
    return rows


def _idea_text(pkg, rows: list[dict]) -> str:
    """The hypothesis as readable text, for a model that has to formalize it into a strategy spec.

    Written as prose on purpose: the downstream system reads a document and proposes a spec, so the clearer the
    statement of what the source actually says, the less it has to invent - and whatever it does invent is
    labelled there as its own suggestion.
    """
    lines = [pkg.strategy_name, "", pkg.hypothesis or ""]
    if pkg.economic_rationale:
        rationale = pkg.economic_rationale.get("value") if isinstance(pkg.economic_rationale, dict) \
            else pkg.economic_rationale
        if rationale:
            lines += ["", f"Economic rationale: {rationale}"]
    stated = [r for r in rows if r["origin"] == "SOURCE"]
    if stated:
        lines += ["", "Rules stated by the source:"]
        for r in stated:
            lines.append(f"- {r['field']}: {r['value']}")
    if pkg.parameters:
        lines += ["", "Parameters stated by the source: " + ", ".join(f"{k}={v}" for k, v in pkg.parameters.items())]
    if pkg.data_requirements:
        lines += ["", "Data required: " + "; ".join(pkg.data_requirements)]
    unresolved = sorted(set(pkg.unknown_rules or []))
    if unresolved:
        lines += ["", "Not stated by the source (leave unresolved rather than guessing): " + ", ".join(unresolved)]
    return "\n".join(str(x) for x in lines if x is not None)


def _local_text(src: Source, facts: list[SourceFact], data_dir: Path, text_dir: Path) -> tuple[Path | None, str]:
    """Provide the document for the other system to read, and say which way it was obtained.

    The HTTP cache is keyed by sha256 of the URL it fetched and is bounded, so the original bytes may be absent
    for an older source. Two ways to answer that, in order of preference:

    ``document``  the cached bytes, so the other system reads the paper itself;
    ``digest``    the verbatim quotes QSD grounded, when the bytes are gone, so values can still be attributed.

    The URL-keyed cache is deliberately not written to: doing so would mean fetching, which is not an export's
    job, and would also suppress the other system's own fetch. A source with neither is reported, not faked.
    """
    if src.content_hash or src.canonical_url or src.url:
        import hashlib

        for url in (src.canonical_url, src.url):
            if not url:
                continue
            cand = data_dir / "http_cache" / f"{hashlib.sha256(url.encode()).hexdigest()}.bin"
            if cand.is_file():
                dest = text_dir / f"source-{src.id}.bin"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cand, dest)
                return dest, "document"
    quoted = [f for f in facts if f.quote]
    if quoted:
        dest = text_dir / f"source-{src.id}-digest.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_evidence_digest(src, quoted), encoding="utf-8")
        return dest, "digest"
    return None, "none"
    return None


@dataclass
class BridgeExport:
    out_dir: Path
    sources: int = 0
    ideas: int = 0
    texts: int = 0
    digests: int = 0
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.warnings = self.warnings or []


def export_bundle(engine: Engine, settings: Settings, out_dir: Path, *,
                  idea_ids: list[int] | None = None) -> BridgeExport:
    """Write sources.jsonl, ideas.jsonl, SOURCES/ and the import contract for the downstream system."""
    result = BridgeExport(out_dir=Path(out_dir))
    result.out_dir.mkdir(parents=True, exist_ok=True)
    text_dir = result.out_dir / "SOURCES"
    data_dir = settings.resolve_path(settings.paths.data_dir)

    with session_scope(engine) as s:
        if idea_ids is None:
            idea_ids = list(s.scalars(select(Idea.id).where(
                Idea.status.in_(["SUBMITTED_TO_BACKTEST", "READY_FOR_FORMALIZATION"])).order_by(Idea.id)))
        packages = []
        for iid in idea_ids:
            idea = s.get(Idea, iid)
            if idea is None:
                result.warnings.append(f"idea {iid} not found; skipped")
                continue
            packages.append((iid, build_package(engine, settings, iid), idea.primary_source_id))

        seen_sources: set[int] = set()
        source_rows, idea_rows = [], []
        for iid, pkg, primary_id in packages:
            facts = {f.fact_type: f for f in s.scalars(select(SourceFact).where(SourceFact.idea_id == iid))}
            if primary_id and primary_id not in seen_sources:
                src = s.get(Source, primary_id)
                if src is not None:
                    seen_sources.add(primary_id)
                    # A source-level digest, so it can carry every quote from the paper rather than only the
                    # ones belonging to whichever idea happened to be exported first.
                    source_facts = list(s.scalars(select(SourceFact).where(SourceFact.source_id == primary_id)))
                    text_path, how = _local_text(src, source_facts, data_dir, text_dir)
                    if how == "document":
                        result.texts += 1
                    elif how == "digest":
                        result.digests += 1
                    else:
                        result.warnings.append(f"source {src.id} ({src.title or 'untitled'}) has no document "
                                               "and no quotes; the importer cannot read it, use the url")
                    # A local scan stores its file path in `url`, and the cache path can end up there too.
                    # Reporting either as the source URL would point the other system at a file on this
                    # machine, so a non-URL is exported as the local path it actually is.
                    raw_url = src.canonical_url or src.url
                    is_url = bool(raw_url) and "://" in str(raw_url)
                    source_rows.append({
                        "qsd_source_id": src.id,
                        "name": src.title or f"source {src.id}",
                        "kind": _KIND_BY_TIER.get(src.tier or 0, "web"),
                        "tier": src.tier,
                        "url": raw_url if is_url else None,
                        "author": None if src.author == UNKNOWN else src.author,
                        "published_on": None if src.publication_date == UNKNOWN else src.publication_date,
                        "local_path": src.local_path or (str(raw_url) if raw_url and not is_url else None),
                        "content_hash": src.content_hash,
                        "text_file": text_path.name if text_path else None,
                        "text_kind": how,
                        "notes": (f"imported from QSD; qsd source id {src.id}; "
                                  f"tier {src.tier} as classified by QSD; text_kind={how}"
                                  + (" (digest = the verbatim quotes QSD grounded, not the full paper)"
                                     if how == "digest" else "")),
                    })
            rows = _rule_rows(pkg, facts)
            if not any(r["field"] in _DECISION_RULES and r["origin"] == "SOURCE" for r in rows):
                result.warnings.append(f"idea {iid} has no source-stated decision rule; importing it would "
                                       "hand over a strategy the source never described")
            idea_rows.append({
                "qsd_idea_id": iid,
                "strategy_id": pkg.strategy_id,
                "qsd_source_id": primary_id,
                "title": pkg.strategy_name,
                "text": _idea_text(pkg, rows),
                "hypothesis": pkg.hypothesis,
                "market": ", ".join(pkg.asset_classes or []),
                "timeframe": pkg.time_horizon,
                "instrument": pkg.known_rules.get("instrument", {}).get("value")
                if isinstance(pkg.known_rules.get("instrument"), dict) else None,
                "entry_concept": pkg.known_rules.get("entry_rule", {}).get("value")
                if isinstance(pkg.known_rules.get("entry_rule"), dict) else None,
                "exit_concept": pkg.known_rules.get("exit_rule", {}).get("value")
                if isinstance(pkg.known_rules.get("exit_rule"), dict) else None,
                "potential_edge": (pkg.economic_rationale or {}).get("value")
                if isinstance(pkg.economic_rationale, dict) else None,
                "known_concerns": json.dumps(pkg.concerns, default=str),
                "rules": rows,
                "unresolved": sorted(set(pkg.unknown_rules or [])),
                "scores": pkg.scores,
                "maturity": pkg.maturity,
                "qsd_status": pkg.status,
                "source_claims_not_validated": (pkg.source_claims or {}).get("claims", {}),
                "warning": pkg.warning,
            })

    _write_jsonl(result.out_dir / "sources.jsonl", source_rows)
    _write_jsonl(result.out_dir / "ideas.jsonl", idea_rows)
    (result.out_dir / "CONTRACT.md").write_text(_contract_text(), encoding="utf-8")
    result.sources, result.ideas = len(source_rows), len(idea_rows)
    return result


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _contract_text() -> str:
    # A plain string, not an f-string: the import example contains braces that must stay literal.
    return (_CONTRACT
            .replace("__BUNDLE_VERSION__", BUNDLE_VERSION)
            .replace("__GENERATED_AT__", datetime.now(UTC).isoformat(timespec="seconds")))


_CONTRACT = """# QSD -> Quant Auto OS import contract (__BUNDLE_VERSION__)

Written __GENERATED_AT__. QSD finds and grounds strategy hypotheses; it does not backtest. This bundle hands
ideas over without touching your database: import them through your own API so your dedupe, status history and
parameter provenance keep working.

## sources.jsonl
| field | meaning |
|---|---|
| `qsd_source_id` | QSD's row id; join key for ideas.jsonl |
| `name`, `author`, `published_on` | normal source fields |
| `url` | the source URL, or null when the source is a local file (then see `local_path`) |
| `local_path` | set only for a local source; not usable from another machine |
| `kind` | `paper` (tier 1-2), `code` (3), `web` (4) |
| `tier` | QSD's classification, 1 = peer-reviewed ... 4 = promotional |
| `content_hash` | QSD's hash of the document it read |
| `text_kind` | `document` (the source's own bytes), `digest` (only QSD's grounded quotes survive), or `none` |
| `text_file` | file under `SOURCES/`, or null if nothing could be obtained |

`text_kind` matters when you formalize: a `document` can be read for anything, while a `digest` is a record of
quoted passages only, so treat a value as sourced from it only when the quote is there. QSD does not fetch
during an export, so a source whose cached bytes have been evicted reports `digest` rather than re-downloading.

## ideas.jsonl
| field | meaning | maps to |
|---|---|---|
| `title` | strategy name | `create_idea(title=...)` |
| `text` | hypothesis + rules + what is unresolved, as prose | `create_idea(text=...)` |
| `market`, `timeframe`, `instrument` | stated scope | the same columns |
| `entry_concept`, `exit_concept` | the source's entry/exit wording, if it stated one | the same columns |
| `potential_edge` | the source's stated rationale | the same columns |
| `known_concerns` | JSON: liquidity, execution, costs, tail risk, red flags | `known_concerns` |
| `rules` | every stated rule: `field`, `value`, **`quote`**, `origin` | see below |
| `unresolved` | rules the source never stated | leave unresolved |
| `scores`, `maturity`, `qsd_status` | QSD's own assessment, for your information | not authoritative |
| `source_claims_not_validated` | performance the source advertised | never rank on this |

## The rule about `rules`
Each entry carries the verbatim `quote` from the source that QSD grounded it against. When you formalize,
record a value that came from one of these as **`SOURCE`**, not `AI_SUGGESTED`: the quote is the evidence, and
it is the main thing this bundle exists to carry. Anything in `unresolved` has no source value, so whatever you
propose for it must be marked `AI_SUGGESTED` (or `DEFAULT`) so it can never be mistaken for the paper's own.

## Suggested import (your API, your venv)
```python
import json, sqlite3
from pathlib import Path
from app.research.inbox import create_idea, register_source
from app.research.sources import SourceRecord

bundle = Path(r"<this folder>")
conn = sqlite3.connect("<your database path>")
by_qsd = {}
for line in (bundle / "sources.jsonl").read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    text_file = row["text_file"]
    rec = SourceRecord(name=row["name"], kind=row["kind"], tier=row["tier"] or 4,
                       url=row["url"], author=row["author"], published_on=row["published_on"],
                       local_path=str(bundle / "SOURCES" / text_file) if text_file else None,
                       notes=row["notes"])
    by_qsd[row["qsd_source_id"]] = register_source(conn, rec, content_hash_value=row["content_hash"])

for line in (bundle / "ideas.jsonl").read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    create_idea(conn, title=row["title"], text=row["text"],
                source_id=by_qsd.get(row["qsd_source_id"]),
                market=row["market"], timeframe=row["timeframe"], instrument=row["instrument"],
                entry_concept=row["entry_concept"], exit_concept=row["exit_concept"],
                potential_edge=row["potential_edge"], known_concerns=row["known_concerns"])
```

`create_idea` refuses an idea with no source, dedupes on content hash, and never lets a proposed value be
attested as verified - which is exactly the behaviour this bundle is built to fit.

## What this bundle does not claim
Nothing here is validated. `scores` are QSD's opinion of how well specified and evidenced an idea is, not a
prediction of profit. No idea has been backtested by QSD and QSD never trades.
"""
