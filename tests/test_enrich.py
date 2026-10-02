"""Source enrichment: a paper is one study however it was reached (spec §24, §56)."""

from __future__ import annotations

from fixtures import make_pdf
from sqlalchemy import select

from qsd.db import init_db, make_engine, session_scope
from qsd.db.models import Source, SourceFact
from qsd.fetch import enrich_source, identifiers_from_url
from qsd.fetch.pipeline import apply_result
from qsd.handlers import parse_bytes, parse_file
from qsd.taxonomy import UNKNOWN, ExtractionMethod


def facts_of(engine, source_id: int) -> dict[str, str]:
    with session_scope(engine) as s:
        return {f.fact_type: f.value for f in s.scalars(select(SourceFact).where(
            SourceFact.source_id == source_id))}


# ---- URL identity -----------------------------------------------------------------------------------

def test_arxiv_identifier_is_version_independent():
    """v2 and v1 of one paper must resolve to a single root-evidence identity (spec §24)."""
    with_v = identifiers_from_url("https://arxiv.org/pdf/1304.6846v2")
    without_v = identifiers_from_url("https://arxiv.org/abs/1304.6846")
    assert with_v == without_v == {"arxiv": "1304.6846"}


def test_arxiv_old_style_identifier():
    assert identifiers_from_url("https://arxiv.org/abs/cs/0701001v3") == {"arxiv": "cs/0701001"}


def test_doi_identifier_from_url():
    assert identifiers_from_url("http://dx.doi.org/10.61190/fsr.v23i2.3131") == \
        {"doi": "10.61190/fsr.v23i2.3131"}


def test_url_without_identifier_yields_nothing():
    assert identifiers_from_url("https://example.com/blog/my-strategy") == {}
    assert identifiers_from_url(None) == {}


# ---- additive enrichment ----------------------------------------------------------------------------

def test_enrichment_derives_identifiers_from_the_source_url(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    with session_scope(engine) as s:
        src = Source(url="https://arxiv.org/abs/1304.6846v2", canonical_url="https://arxiv.org/abs/1304.6846")
        s.add(src)
        s.flush()
        added = enrich_source(s, src)
        sid = src.id
    assert "ID_ARXIV" in added
    assert facts_of(engine, sid)["ID_ARXIV"] == "1304.6846"


def test_enrichment_uses_a_pdf_url_fact_when_the_page_url_is_opaque(tmp_path):
    """A publisher landing page hides the identity; the stored PDF link often does not."""
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    with session_scope(engine) as s:
        src = Source(url="https://www.somejournal.com/article/12345", canonical_url="https://www.somejournal.com/a")
        s.add(src)
        s.flush()
        s.add(SourceFact(source_id=src.id, fact_type="PDF_URL", value="https://arxiv.org/pdf/2507.15876v1",
                         extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))
        s.flush()
        enrich_source(s, src)
        sid = src.id
    assert facts_of(engine, sid)["ID_ARXIV"] == "2507.15876"


def test_enrichment_never_overwrites_known_values(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    with session_scope(engine) as s:
        src = Source(url="https://arxiv.org/abs/1304.6846", title="A real title", author="A. Author",
                     publication_date="2013-04-25")
        s.add(src)
        s.flush()
        s.add(SourceFact(source_id=src.id, fact_type="ID_ARXIV", value="9999.99999",
                         extraction_method=ExtractionMethod.DETERMINISTIC, confidence=1.0))
        s.flush()
        added = enrich_source(s, src, result=None)
        s.flush()
        assert src.title == "A real title" and src.author == "A. Author"
        assert src.publication_date == "2013-04-25"
        # a known identifier is never substituted, and enrichment must not claim it added one
        assert "ID_ARXIV" not in added
        facts = {f.fact_type: f.value for f in s.scalars(select(SourceFact).where(SourceFact.source_id == src.id))}
        assert facts["ID_ARXIV"] == "9999.99999"


def test_url_only_source_never_stays_titled_unknown(tmp_path):
    """An untitled row is invisible on the dashboard and reads as an unnamed video/paper."""
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    with session_scope(engine) as s:
        src = Source(canonical_url="https://arxiv.org/pdf/2509.05080v3")
        s.add(src)
        s.flush()
        enrich_source(s, src)
        assert src.title == "arXiv:2509.05080"


def test_local_file_gets_a_document_derived_title(tmp_path):
    """When the document declares no title, the filename is a truthful fallback — never left as UNKNOWN."""
    path = tmp_path / "mesfin_2026_structural_limits_mnq.pdf"
    path.write_bytes(make_pdf(["Structural limits in MNQ futures. We short when ..."], title=""))
    result = parse_file(path)
    assert result.metadata["title"] == UNKNOWN  # handlers use UNKNOWN as their "nothing found" sentinel
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    with session_scope(engine) as s:
        src = Source(local_path=str(path), title=UNKNOWN)
        s.add(src)
        s.flush()
        apply_result(src, result)
        added = enrich_source(s, src, result, local_path=path)
        assert "title" in added
        assert src.title == "mesfin 2026 structural limits mnq"


def test_enrichment_keeps_the_title_the_document_declared(tmp_path):
    path = tmp_path / "misleading_filename.pdf"
    path.write_bytes(make_pdf(["Momentum text."], title="Momentum Study"))
    result = parse_file(path)
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    with session_scope(engine) as s:
        src = Source(local_path=str(path), title=UNKNOWN)
        s.add(src)
        s.flush()
        apply_result(src, result)
        enrich_source(s, src, result, local_path=path)
        assert src.title == "Momentum Study"  # the document's own title beats the filename


def test_enrichment_leaves_a_titled_source_alone(tmp_path):
    path = tmp_path / "whatever.pdf"
    path.write_bytes(make_pdf(["Some text about momentum."]))
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    with session_scope(engine) as s:
        src = Source(local_path=str(path), title="Kept title")
        s.add(src)
        s.flush()
        assert "title" not in enrich_source(s, src, parse_file(path), local_path=path)
        assert src.title == "Kept title"


def test_fetch_pipeline_enriches_a_read_pdf(tmp_path):
    """A PDF reached by direct URL still gets its identity recorded."""
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    with session_scope(engine) as s:
        src = Source(url="https://arxiv.org/pdf/2507.15876v1", canonical_url="https://arxiv.org/pdf/2507.15876v1")
        s.add(src)
        s.flush()
        result = parse_bytes(make_pdf(["Trend factors in CTA strategies."]), name="2507.15876v1.pdf")
        enrich_source(s, src, result)
        sid = src.id
    assert facts_of(engine, sid)["ID_ARXIV"] == "2507.15876"
