"""AI layer: provider adapters, cached and budgeted gateway, two-stage grounded extraction (spec §83–§89)."""

from .extract import ExtractionReport, RegroundReport, extract_ideas, idea_summary, reground_source
from .gateway import AIGateway, AIOutputError, CallInfo, UnpricedModelError
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
    "ExtractionReport", "RegroundReport", "extract_ideas", "reground_source", "GroundingReport", "idea_summary",
    "AIGateway", "AIOutputError", "CallInfo",
    "UnpricedModelError", "ground_strategy", "normalize", "quote_in_source", "AnthropicProvider",
    "OpenAICompatibleProvider", "Provider", "ProviderError", "ProviderRefusal", "ProviderResponse",
    "default_providers", "ExtractedStrategy", "StageAResult", "StageBResult",
]
