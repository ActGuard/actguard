from __future__ import annotations

from typing import Literal

from actguard.exceptions import ReportingContractError

MetricSource = Literal["deterministic", "attributed", "mixed"]

CANONICAL_USAGE_EVENT = ("llm", "usage")
ROOT_ONLY_BUCKET = "root_only"
UNATTRIBUTED_BUCKET = "unattributed"

DETERMINISTIC_METRICS = frozenset(
    {
        "total_spend",
        "average_spend_per_run",
        "budget_used_percentage",
        "spend_over_time",
        "run_total_spend",
        "workspace_total_spend",
        "agent_total_spend",
    }
)

ATTRIBUTED_METRICS = frozenset(
    {
        "spend_by_scope",
        "spend_by_tool",
        "spend_by_subagent",
        "run_spend_breakdown",
        "attributed_activity_timeline",
        "root_only_analysis",
    }
)

MIXED_RESPONSE_FIELDS = frozenset(
    {
        "total_usd_micros",
        "attributed_usd_micros",
        "root_only_usd_micros",
        "unattributed_usd_micros",
    }
)


def metric_source(metric_name: str) -> MetricSource:
    if metric_name in DETERMINISTIC_METRICS:
        return "deterministic"
    if metric_name in ATTRIBUTED_METRICS:
        return "attributed"
    if metric_name in MIXED_RESPONSE_FIELDS:
        return "mixed"
    raise ReportingContractError(f"Unknown reporting metric: {metric_name}")


def is_attributed_spend_event(*, category: str, name: str, usd_micros: int | None) -> bool:
    return (category, name) == CANONICAL_USAGE_EVENT and usd_micros is not None
