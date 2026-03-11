from __future__ import annotations

import pytest

from actguard.events.catalog import SIGNIFICANT, VERBOSE
from actguard.reporting_contract import (
    ATTRIBUTED_METRICS,
    CANONICAL_USAGE_EVENT,
    DETERMINISTIC_METRICS,
    MIXED_RESPONSE_FIELDS,
    ROOT_ONLY_BUCKET,
    UNATTRIBUTED_BUCKET,
    is_attributed_spend_event,
    metric_source,
)


def test_reporting_metric_source_matrix_is_explicit():
    assert metric_source("total_spend") == "deterministic"
    assert metric_source("spend_by_scope") == "attributed"
    assert metric_source("total_usd_micros") == "mixed"


def test_reporting_contract_constants_cover_step_four_terms():
    assert CANONICAL_USAGE_EVENT == ("llm", "usage")
    assert ROOT_ONLY_BUCKET == "root_only"
    assert UNATTRIBUTED_BUCKET == "unattributed"
    assert "total_spend" in DETERMINISTIC_METRICS
    assert "spend_by_tool" in ATTRIBUTED_METRICS
    assert "unattributed_usd_micros" in MIXED_RESPONSE_FIELDS


def test_only_llm_usage_rows_are_attributed_spend_events():
    assert is_attributed_spend_event(category="llm", name="usage", usd_micros=1)
    assert not is_attributed_spend_event(category="tool", name="invoke", usd_micros=1)
    assert not is_attributed_spend_event(category="llm", name="usage", usd_micros=None)


def test_unknown_reporting_metric_raises():
    with pytest.raises(KeyError):
        metric_source("mystery_metric")


def test_event_catalog_contains_only_canonical_runtime_names():
    assert "run.start" in SIGNIFICANT
    assert "run.end" in SIGNIFICANT
    assert "tool.invoke" in SIGNIFICANT
    assert "budget.released" not in VERBOSE
    assert "guard.max_attempts_exceeded" not in VERBOSE
    assert "policy.blocked" not in VERBOSE
    assert "run.started" not in VERBOSE
    assert "run.completed" not in VERBOSE
    assert "tool.invoked" not in VERBOSE
    assert "tool.succeeded" not in VERBOSE
