"""Research database schema (spec §27, §28, §29, §90–§93, §112, §120, §133, §137).

Conventions:
- Descriptive text that may be missing defaults to "UNKNOWN" (spec §0, §47); it is never guessed.
- Numeric scores default to NULL, meaning "not scored yet" (distinct from a score of 0).
- External performance is stored only in claimed_* columns as the source's own wording (spec §82).
- No column may hold credentials, cookies or tokens (spec §13–§15); tests enforce this by column name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

from ..taxonomy import (
    UNKNOWN,
    AccessStatus,
    CampaignStatus,
    ErrorState,
    ExtractionMethod,
    IdeaSourceRole,
    IdeaStatus,
    MarketRegime,
    PositionDirection,
    RegimeBasis,
    RegimeSuitability,
    RejectionReason,
    RequestClass,
    SourceRelation,
    TimeHorizon,
)

SCHEMA_VERSION = 1
MAX_QUOTE_CHARS = 300  # copyright: short necessary quotations only (spec §108)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _enum(e: type[StrEnum]) -> Enum:
    # Stored as plain strings (portable to Postgres later); values validated on write.
    return Enum(e, native_enum=False, validate_strings=True, length=40, values_callable=lambda x: [m.value for m in x])


def _unknown_text() -> Mapped[str]:
    return mapped_column(Text, nullable=False, default=UNKNOWN)


def _score() -> Mapped[float | None]:
    return mapped_column(Float, nullable=True)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class SchemaMeta(Base):
    __tablename__ = "schema_meta"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class Campaign(Base):
    """A research campaign (spec §94, §95, §129)."""

    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_text: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(40), default="QUICK_DISCOVERY")
    asset_classes: Mapped[list] = mapped_column(default=list)
    strategy_families: Mapped[list] = mapped_column(default=list)
    target_regimes: Mapped[list] = mapped_column(default=list)
    budgets: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[CampaignStatus] = mapped_column(_enum(CampaignStatus), default=CampaignStatus.PLANNED)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Source(Base):
    """One retrieved or indexed source with provenance (spec §9, §27)."""

    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"))
    title: Mapped[str] = _unknown_text()
    author: Mapped[str] = _unknown_text()
    organization: Mapped[str] = _unknown_text()
    url: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text, unique=True)
    local_path: Mapped[str | None] = mapped_column(Text)
    publication_date: Mapped[str] = _unknown_text()  # as given by the source; may be partial ("2019")
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tier: Mapped[int | None] = mapped_column(Integer)  # 1..5 (spec §24); NULL = not tiered yet
    content_type: Mapped[str] = _unknown_text()
    format: Mapped[str] = _unknown_text()
    file_size: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    handler_version: Mapped[str | None] = mapped_column(String(40))
    source_version: Mapped[str] = _unknown_text()
    license: Mapped[str] = _unknown_text()
    access_status: Mapped[AccessStatus] = mapped_column(_enum(AccessStatus), default=AccessStatus.NOT_FETCHED)
    source_quality_score: Mapped[float | None] = _score()
    evidence_quality_score: Mapped[float | None] = _score()
    asset_classes: Mapped[list] = mapped_column(default=list)
    strategy_families: Mapped[list] = mapped_column(default=list)
    root_evidence_id: Mapped[str | None] = mapped_column(String(64), index=True)  # spec §70
    original_source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))  # spec §71
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (CheckConstraint("tier IS NULL OR (tier BETWEEN 1 AND 5)", name="ck_source_tier"),)


class SourceLink(Base):
    """Source graph edge (spec §29)."""

    __tablename__ = "source_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    from_source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    to_source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    relation: Mapped[SourceRelation] = mapped_column(_enum(SourceRelation))
    __table_args__ = (UniqueConstraint("from_source_id", "to_source_id", "relation"),)


class FetchLog(Base):
    """Every network retrieval, redacted metadata only (spec §15). No headers or bodies are stored."""

    __tablename__ = "fetch_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    page_url: Mapped[str | None] = mapped_column(Text)
    request_url: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(10), default="GET")
    status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(200))
    retrieval_method: Mapped[str] = mapped_column(String(40))  # API, RSS, HTTP, BROWSER, DEVTOOLS, LOCAL
    request_class: Mapped[RequestClass] = mapped_column(_enum(RequestClass), default=RequestClass.UNKNOWN)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[str | None] = mapped_column(Text)


class Idea(Base):
    """A strategy idea (spec §46, §90). Rule fields default to UNKNOWN (spec §47)."""

    __tablename__ = "ideas"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"))
    primary_source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    root_evidence_id: Mapped[str | None] = mapped_column(String(64), index=True)
    strategy_name: Mapped[str] = _unknown_text()
    summary: Mapped[str] = _unknown_text()
    asset_classes: Mapped[list] = mapped_column(default=list)
    strategy_families: Mapped[list] = mapped_column(default=list)
    diversification_tags: Mapped[list] = mapped_column(default=list)  # spec §79
    position_direction: Mapped[PositionDirection] = mapped_column(
        _enum(PositionDirection), default=PositionDirection.UNKNOWN
    )
    time_horizon: Mapped[TimeHorizon] = mapped_column(_enum(TimeHorizon), default=TimeHorizon.UNKNOWN)

    # Rules and specification (spec §46)
    instrument: Mapped[str] = _unknown_text()
    universe: Mapped[str] = _unknown_text()
    timeframe: Mapped[str] = _unknown_text()
    data_frequency: Mapped[str] = _unknown_text()
    indicators: Mapped[str] = _unknown_text()
    signal: Mapped[str] = _unknown_text()
    lookback: Mapped[str] = _unknown_text()
    entry_rule: Mapped[str] = _unknown_text()
    exit_rule: Mapped[str] = _unknown_text()
    stop_rule: Mapped[str] = _unknown_text()
    take_profit_rule: Mapped[str] = _unknown_text()
    position_sizing: Mapped[str] = _unknown_text()
    rebalance: Mapped[str] = _unknown_text()
    order_type: Mapped[str] = _unknown_text()
    trading_session: Mapped[str] = _unknown_text()
    holding_period: Mapped[str] = _unknown_text()
    transaction_cost_assumption: Mapped[str] = _unknown_text()
    liquidity_requirement: Mapped[str] = _unknown_text()
    portfolio_rules: Mapped[str] = _unknown_text()
    risk_rules: Mapped[str] = _unknown_text()
    parameters: Mapped[dict] = mapped_column(default=dict)  # name -> value as stated, or "UNKNOWN"
    data_required: Mapped[list] = mapped_column(default=list)
    unknown_rules: Mapped[list] = mapped_column(default=list)  # explicit list of missing pieces

    # Rationale (spec §49, §50)
    economic_rationale: Mapped[str] = _unknown_text()
    rationale_confidence: Mapped[float | None] = _score()
    why_edge_persists: Mapped[str] = _unknown_text()

    # Source claims, never verified (spec §82)
    claimed_sharpe: Mapped[str | None] = mapped_column(String(100))
    claimed_cagr: Mapped[str | None] = mapped_column(String(100))
    claimed_max_dd: Mapped[str | None] = mapped_column(String(100))
    claimed_win_rate: Mapped[str | None] = mapped_column(String(100))
    claimed_pf: Mapped[str | None] = mapped_column(String(100))

    # Pre-screen assessments (spec §56–§68); text categories until M6 defines scales
    point_in_time_feasible: Mapped[str] = _unknown_text()
    liquidity_score: Mapped[float | None] = _score()
    exit_feasibility: Mapped[float | None] = _score()
    cost_category: Mapped[str] = _unknown_text()
    tail_risk: Mapped[str] = _unknown_text()
    capacity: Mapped[str] = _unknown_text()
    automation_feasibility: Mapped[str] = _unknown_text()
    retail_implementability: Mapped[float | None] = _score()
    opportunity_frequency: Mapped[str] = _unknown_text()
    expected_robustness: Mapped[str] = _unknown_text()
    red_flags: Mapped[list] = mapped_column(default=list)

    # Scores 0–100 (NULL = not scored yet)
    source_quality: Mapped[float | None] = _score()
    evidence_quality: Mapped[float | None] = _score()
    formalization_completeness: Mapped[float | None] = _score()
    parameter_complexity: Mapped[float | None] = _score()
    replication_score: Mapped[float | None] = _score()
    novelty_score: Mapped[float | None] = _score()
    idea_quality_score: Mapped[float | None] = _score()
    research_priority_score: Mapped[float | None] = _score()
    search_depth_level: Mapped[int] = mapped_column(Integer, default=0)  # spec §43, 0..5

    # Dedupe (spec §69)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("ideas.id"))
    dedupe_class: Mapped[str] = mapped_column(String(20), default="NEW")  # NEW/VARIANT/DUPLICATE/MINOR_VARIATION

    status: Mapped[IdeaStatus] = mapped_column(_enum(IdeaStatus), default=IdeaStatus.DISCOVERED, index=True)
    hard_fail_reasons: Mapped[list] = mapped_column(default=list)
    next_research_action: Mapped[str] = _unknown_text()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    regimes: Mapped[list[IdeaRegime]] = relationship(back_populates="idea", cascade="all, delete-orphan")

    __table_args__ = (CheckConstraint("search_depth_level BETWEEN 0 AND 5", name="ck_idea_depth"),)


class IdeaRegime(Base):
    """Market-regime suitability per idea (spec §137)."""

    __tablename__ = "idea_regimes"
    id: Mapped[int] = mapped_column(primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id"))
    regime: Mapped[MarketRegime] = mapped_column(_enum(MarketRegime))
    suitability: Mapped[RegimeSuitability] = mapped_column(
        _enum(RegimeSuitability), default=RegimeSuitability.UNKNOWN
    )
    basis: Mapped[RegimeBasis] = mapped_column(_enum(RegimeBasis), default=RegimeBasis.UNKNOWN)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source_fact_id: Mapped[int | None] = mapped_column(ForeignKey("source_facts.id"))
    idea: Mapped[Idea] = relationship(back_populates="regimes")

    __table_args__ = (
        UniqueConstraint("idea_id", "regime"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_regime_confidence"),
    )


class IdeaSource(Base):
    """Which sources describe / support / contradict an idea (spec §29, §72, §73)."""

    __tablename__ = "idea_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id"))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    role: Mapped[IdeaSourceRole] = mapped_column(_enum(IdeaSourceRole))
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("idea_id", "source_id", "role"),)


class SourceFact(Base):
    """An extracted fact/rule with its exact location (spec §28)."""

    __tablename__ = "source_facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    idea_id: Mapped[int | None] = mapped_column(ForeignKey("ideas.id"))
    fact_type: Mapped[str] = mapped_column(String(40))  # ENTRY_RULE, EXIT_RULE, CLAIMED_CAGR, REGIME, ...
    value: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    slide: Mapped[int | None] = mapped_column(Integer)
    timestamp_start: Mapped[str | None] = mapped_column(String(20))  # "08:31"
    timestamp_end: Mapped[str | None] = mapped_column(String(20))
    section: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)  # free-form fallback (e.g. CSS path, cell ref)
    quote: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(_enum(ExtractionMethod))
    model: Mapped[str | None] = mapped_column(String(80))
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_fact_confidence"),)

    @validates("quote")
    def _short_quote(self, _key: str, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_QUOTE_CHARS:
            raise ValueError(f"quote longer than {MAX_QUOTE_CHARS} chars; store a reference instead (spec §108)")
        return value


class IdeaStatusHistory(Base):
    __tablename__ = "idea_status_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id"))
    from_status: Mapped[IdeaStatus | None] = mapped_column(_enum(IdeaStatus))
    to_status: Mapped[IdeaStatus] = mapped_column(_enum(IdeaStatus))
    reason: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Rejection(Base):
    """Why an idea was rejected. Never deleted (spec §92)."""

    __tablename__ = "rejections"
    id: Mapped[int] = mapped_column(primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id"))
    reason: Mapped[RejectionReason] = mapped_column(_enum(RejectionReason))
    detail: Mapped[str] = mapped_column(Text, default="")
    is_hard_fail: Mapped[bool] = mapped_column(Boolean, default=False)
    rejected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchQuery(Base):
    """Search memory (spec §93, §121)."""

    __tablename__ = "search_queries"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"))
    connector: Mapped[str] = mapped_column(String(40))  # arxiv, openalex, crossref, rss, ...
    query_text: Mapped[str] = mapped_column(Text)
    query_hash: Mapped[str] = mapped_column(String(64), index=True)  # connector + normalized query
    purpose: Mapped[str] = mapped_column(String(40), default="DISCOVERY")  # DISCOVERY/REPLICATION/CONTRADICTION/...
    results_count: Mapped[int] = mapped_column(Integer, default=0)
    useful_count: Mapped[int] = mapped_column(Integer, default=0)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AICall(Base):
    """AI cost ledger (spec §84, §120)."""

    __tablename__ = "ai_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    idea_id: Mapped[int | None] = mapped_column(ForeignKey("ideas.id"))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    task: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(20))
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AICache(Base):
    """Cached AI results keyed by content hash + prompt version + model + task (spec §85)."""

    __tablename__ = "ai_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    task: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(20))
    content_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ErrorRecord(Base):
    """No silent failures (spec §133)."""

    __tablename__ = "errors"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"))
    stage: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str | None] = mapped_column(Text)  # URL, path or source id
    handler: Mapped[str | None] = mapped_column(String(60))
    error: Mapped[str] = mapped_column(Text)  # must be passed through logging_setup.redact
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[ErrorState] = mapped_column(_enum(ErrorState), default=ErrorState.UNRESOLVED, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LocalFile(Base):
    """Index of user-provided local files; read-only scanning (spec §111, §112)."""

    __tablename__ = "local_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    format: Mapped[str] = _unknown_text()
    size: Mapped[int] = mapped_column(Integer)
    mtime: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = _unknown_text()
    author: Mapped[str] = _unknown_text()
    topics: Mapped[list] = mapped_column(default=list)
    strategy_families: Mapped[list] = mapped_column(default=list)
    processed_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
