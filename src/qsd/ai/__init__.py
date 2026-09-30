"""AI layer: provider adapters, cached and budgeted gateway, two-stage grounded extraction (spec §83–§89)."""

from .extract import ExtractionReport, extract_ideas, idea_summary
from .gateway import AIGateway, AIOutputError, CallInfo, UnpricedModelError
from .grounding import ground_strategy, normalize, quote_in_source
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
    "ExtractionReport", "extract_ideas", "idea_summary", "AIGateway", "AIOutputError", "CallInfo",
    "UnpricedModelError", "ground_strategy", "normalize", "quote_in_source", "AnthropicProvider",
    "OpenAICompatibleProvider", "Provider", "ProviderError", "ProviderRefusal", "ProviderResponse",
    "default_providers", "ExtractedStrategy", "StageAResult", "StageBResult",
]
