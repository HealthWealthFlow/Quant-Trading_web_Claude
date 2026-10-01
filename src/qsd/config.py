"""Configuration loading: config/default.yaml -> optional config/local.yaml -> QSD_* environment variables.

Secrets (API keys) are never read from YAML; they come only from the environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from .taxonomy import AssetClass

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "default.yaml"
LOCAL_CONFIG = REPO_ROOT / "config" / "local.yaml"
ENV_PREFIX = "QSD_"

ASSET_CLASSES = {a.value for a in AssetClass}


class Paths(BaseModel):
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    local_sources: list[Path] = Field(default_factory=list)


class AssetClassConfig(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["STOCK", "ETF", "OPTIONS", "FOREX", "CRYPTO"])

    @model_validator(mode="after")
    def _known(self) -> AssetClassConfig:
        unknown = set(self.enabled) - ASSET_CLASSES
        if unknown:
            raise ValueError(f"Unknown asset classes: {sorted(unknown)}")
        return self


class ModelPrice(BaseModel):
    input_per_mtok: float = Field(ge=0)   # USD per 1M input tokens
    output_per_mtok: float = Field(ge=0)  # USD per 1M output tokens


class AIConfig(BaseModel):
    default_provider: str = "deepseek"
    cheap_model: str = "deepseek-chat"
    strong_model: str = "deepseek-chat"
    second_opinion_provider: str | None = None
    second_opinion_model: str = "claude-opus-5-5"
    stage_a_max_chars: int = Field(6000, gt=0)
    stage_b_max_chars: int = Field(40000, gt=0)
    stage_a_max_tokens: int = Field(800, gt=0)
    stage_b_max_tokens: int = Field(6000, gt=0)
    # A model without a price is never called: budgets could not be enforced (spec §45).
    prices: dict[str, ModelPrice | None] = Field(default_factory=dict)


class Budgets(BaseModel):
    max_ai_cost_usd_per_day: float = Field(1.0, ge=0)
    max_ai_cost_usd_per_month: float = Field(20.0, ge=0)
    max_ai_cost_usd_per_campaign: float = Field(2.0, ge=0)
    max_ai_calls_per_campaign: int = Field(200, ge=0)
    max_ai_tokens_per_campaign: int = Field(2_000_000, ge=0)
    max_search_requests_per_campaign: int = Field(100, ge=0)
    max_urls_per_campaign: int = Field(300, ge=0)
    max_documents_per_campaign: int = Field(150, ge=0)
    max_sources_per_domain: int = Field(25, ge=0)
    max_runtime_minutes_per_campaign: int = Field(60, ge=0)
    max_video_minutes_per_campaign: int = Field(0, ge=0)
    max_audio_minutes_per_campaign: int = Field(0, ge=0)


class Crawling(BaseModel):
    user_agent: str = "QSD-Research-Bot/0.1"
    respect_robots_txt: bool = True
    requests_per_minute_per_domain: int = Field(10, gt=0)
    max_retries: int = Field(3, ge=0)
    backoff_base_seconds: float = Field(2.0, gt=0)
    request_timeout_seconds: int = Field(30, gt=0)
    max_download_mb: int = Field(50, gt=0)

    @model_validator(mode="after")
    def _robots_mandatory(self) -> Crawling:
        # Safety policy (spec §104, §134): not switchable off by configuration.
        if not self.respect_robots_txt:
            raise ValueError("respect_robots_txt cannot be disabled")
        return self


class Exploration(BaseModel):
    known_quality: float = 0.7
    new_promising: float = 0.2
    experimental: float = 0.1

    @model_validator(mode="after")
    def _sums_to_one(self) -> Exploration:
        total = self.known_quality + self.new_promising + self.experimental
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"exploration shares must sum to 1.0, got {total}")
        return self


class QualityGate(BaseModel):
    high_priority: int = 85
    promising: int = 70
    research_further: int = 55


class Discovery(BaseModel):
    # Sent to OpenAlex/Crossref "polite pool" as the API docs request. Leave empty to omit.
    contact_email: str | None = None
    results_per_query: int = Field(10, gt=0, le=100)
    search_memory_days: int = Field(30, ge=0)  # don't repeat an identical search within this window
    # YouTube Data API (metadata only: titles, descriptions, links). Used when YOUTUBE_API_KEY is set.
    youtube_enabled: bool = True
    youtube_results_per_query: int = Field(10, gt=0, le=50)
    youtube_max_links_per_video: int = Field(5, ge=0, le=20)  # research links followed from a description


DEFAULT_IDEA_WEIGHTS = {
    "economic_rationale": 12, "rule_quantifiability": 10, "point_in_time": 8, "data_availability": 8,
    "liquidity": 10, "exit_executability": 8, "edge_vs_cost": 10, "parameter_simplicity": 6,
    "expected_robustness": 6, "opportunity_frequency": 5, "tail_risk": 5, "capacity": 4,
    "automation": 4, "diversification": 4,
}


class Scoring(BaseModel):
    idea_weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_IDEA_WEIGHTS))  # spec §52
    min_coverage_for_gate: float = Field(0.6, ge=0, le=1)  # below this, ideas are researched further, not archived

    @model_validator(mode="after")
    def _weights(self) -> Scoring:
        unknown = set(self.idea_weights) - set(DEFAULT_IDEA_WEIGHTS)
        if unknown:
            raise ValueError(f"unknown idea weight keys: {sorted(unknown)}")
        if abs(sum(self.idea_weights.values()) - 100) > 1e-6:
            raise ValueError("idea_weights must sum to 100")
        return self


class Handoff(BaseModel):
    """Backtest handoff rule (spec §123)."""

    min_completeness: float = Field(60, ge=0, le=100)
    min_normalized_quality: float = Field(55, ge=0, le=100)
    min_coverage: float = Field(0.6, ge=0, le=1)
    min_data_availability: float = Field(0.4, ge=0, le=1)
    queue_dir: Path = Path("data/backtest_queue")


class Export(BaseModel):
    """Optional Markdown notes (e.g. an Obsidian vault). Files written by QSD carry `qsd_id` in their front matter;
    any other file is never overwritten."""

    notes_dir: Path | None = None
    notes_subfolder: str = "QSD Strategies"


class Settings(BaseModel):
    paths: Paths = Field(default_factory=Paths)
    asset_classes: AssetClassConfig = Field(default_factory=AssetClassConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    budgets: Budgets = Field(default_factory=Budgets)
    crawling: Crawling = Field(default_factory=Crawling)
    exploration: Exploration = Field(default_factory=Exploration)
    quality_gate: QualityGate = Field(default_factory=QualityGate)
    discovery: Discovery = Field(default_factory=Discovery)
    scoring: Scoring = Field(default_factory=Scoring)
    handoff: Handoff = Field(default_factory=Handoff)
    export: Export = Field(default_factory=Export)

    def resolve_path(self, p: Path) -> Path:
        return p if p.is_absolute() else REPO_ROOT / p


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides(environ: dict[str, str]) -> dict[str, Any]:
    """QSD_BUDGETS__MAX_AI_CALLS_PER_CAMPAIGN=50 -> {"budgets": {"max_ai_calls_per_campaign": "50"}}."""
    out: dict[str, Any] = {}
    for key, value in environ.items():
        if not key.startswith(ENV_PREFIX) or "__" not in key:
            continue
        parts = key[len(ENV_PREFIX):].lower().split("__")
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def load_settings(
    config_path: Path | None = None,
    local_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Settings:
    merged = _read_yaml(config_path or DEFAULT_CONFIG)
    merged = _deep_merge(merged, _read_yaml(local_path or LOCAL_CONFIG))
    merged = _deep_merge(merged, _env_overrides(dict(os.environ if environ is None else environ)))
    return Settings.model_validate(merged)


def get_secret(name: str, environ: dict[str, str] | None = None) -> str | None:
    """API keys come only from the environment (or a .env loaded into it). Returns None if unset."""
    value = (os.environ if environ is None else environ).get(name, "").strip()
    return value or None
