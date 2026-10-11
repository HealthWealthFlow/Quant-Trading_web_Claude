"""Research packages and backtest queue handoff (spec §122–§125). No broker or live-trading path exists here."""

from .bridge import BUNDLE_VERSION, BridgeExport, export_bundle
from .package import (
    DOWNSTREAM_WARNING,
    ResearchPackage,
    build_package,
    export_schema,
    handoff_check,
    handoff_report,
    submit_to_queue,
    suitable_regimes,
    withdraw_from_queue,
)

__all__ = ["BUNDLE_VERSION", "DOWNSTREAM_WARNING", "BridgeExport", "ResearchPackage", "build_package",
           "export_bundle", "export_schema", "handoff_check", "handoff_report", "submit_to_queue",
           "suitable_regimes", "withdraw_from_queue"]
