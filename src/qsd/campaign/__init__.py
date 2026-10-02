"""Research campaigns: request → discovery → extraction → scoring → deepening, with budgets and resume."""

from .harvest import HarvestReport, promising_ideas, run_harvest
from .parse import CampaignSpec, parse_request
from .runner import CampaignLimits, CampaignReport, CampaignRunner

__all__ = ["CampaignSpec", "parse_request", "CampaignLimits", "CampaignReport", "CampaignRunner",
           "HarvestReport", "run_harvest", "promising_ideas"]
