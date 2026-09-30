"""Source discovery: official API connectors, feeds, query families, search memory, budgets, tiering."""

from .budget import BudgetExhausted, CampaignBudget
from .connectors import (
    CONNECTORS,
    ArxivConnector,
    Candidate,
    ConnectorError,
    CrossrefConnector,
    FeedConnector,
    OpenAlexConnector,
    candidates_from_urls,
)
from .queries import build_queries, expand_query, normalize_query, query_hash
from .runner import DiscoveryReport, run_discovery, store_candidate
from .tiering import tier_for_candidate, tier_for_url

__all__ = [
    "BudgetExhausted", "CampaignBudget", "CONNECTORS", "ArxivConnector", "Candidate", "ConnectorError",
    "CrossrefConnector", "FeedConnector", "OpenAlexConnector", "candidates_from_urls", "build_queries",
    "expand_query", "normalize_query", "query_hash", "DiscoveryReport", "run_discovery", "store_candidate",
    "tier_for_candidate", "tier_for_url",
]
