"""AI layer: provider adapters, cached and budgeted gateway, two-stage grounded extraction (spec §83–§89)."""

from .extract import (
    ExtractionReport,
    ReextractReport,
    RegroundReport,
    extract_ideas,
    idea_summary,
    reextract_source,
    reground_source,
    stored_extraction,
)
from .gateway import AIGateway, AIOutputError, AIOutputTruncated, CallInfo, UnpricedModelError
from .grounding import GroundingReport, ground_strategy, normalize, quote_in_source
from .providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
    ProviderRefusal,
    ProviderResponse,
    default_providers,
)
from .schemas import ExtractedStrategy, StageAResult, StageBResult

__all__ = [
    "ExtractionReport", "ReextractReport", "RegroundReport", "extract_ideas", "reextract_source", "reground_source",
    "stored_extraction", "GroundingReport", "idea_summary",
    "AIGateway", "AIOutputError", "AIOutputTruncated", "CallInfo",
    "UnpricedModelError", "ground_strategy", "normalize", "quote_in_source", "AnthropicProvider",
    "OpenAICompatibleProvider", "Provider", "ProviderError", "ProviderRefusal", "ProviderResponse",
    "default_providers", "ExtractedStrategy", "StageAResult", "StageBResult",
]
