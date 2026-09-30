"""Research campaigns: request → discovery → extraction → scoring → deepening, with budgets and resume."""

from .parse import CampaignSpec, parse_request
from .runner import CampaignLimits, CampaignReport, CampaignRunner

__all__ = ["CampaignSpec", "parse_request", "CampaignLimits", "CampaignReport", "CampaignRunner"]
