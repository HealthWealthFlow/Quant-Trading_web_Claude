"""Research packages and backtest queue handoff (spec §122–§125). No broker or live-trading path exists here."""

from .package import (
    DOWNSTREAM_WARNING,
    ResearchPackage,
    build_package,
    export_schema,
    handoff_check,
    submit_to_queue,
    suitable_regimes,
)

__all__ = ["DOWNSTREAM_WARNING", "ResearchPackage", "build_package", "export_schema", "handoff_check",
           "submit_to_queue", "suitable_regimes"]
