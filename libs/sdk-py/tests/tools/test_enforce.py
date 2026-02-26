"""Tests for the @enforce decorator and Rule classes."""

import pytest

import actguard
from actguard.exceptions import GuardError
from actguard.tools._facts import mint

# ---------------------------------------------------------------------------
# Tests — RequireFact
# ---------------------------------------------------------------------------


def test_require_fact_blocks_missing():
    @actguard.enforce([actguard.RequireFact("order_id", "order_id")])
    def delete_order(order_id: str):
        return f"deleted:{order_id}"

    with actguard.session("sess-1"):
        with pytest.raises(GuardError) as exc_info:
            delete_order(order_id="o99")

    assert exc_info.value.code == "MISSING_FACT"
    assert "o99" in exc_info.value.to_prompt()
    assert "order_id" in exc_info.value.to_prompt()


def test_require_fact_passes_after_mint():
    @actguard.enforce([actguard.RequireFact("order_id", "order_id")])
    def delete_order(order_id: str):
        return f"deleted:{order_id}"

    with actguard.session("sess-2"):
        mint("sess-2", "global", "order_id", "o1", ttl=60)
        result = delete_order(order_id="o1")

    assert result == "deleted:o1"


def test_require_fact_batch_list():
    @actguard.enforce([actguard.RequireFact("ids", "item_id")])
    def delete_items(ids: list):
        return f"deleted:{ids}"

    with actguard.session("sess-3"):
        mint("sess-3", "global", "item_id", "a", ttl=60)
        mint("sess-3", "global", "item_id", "b", ttl=60)
        result = delete_items(ids=["a", "b"])

    assert "deleted" in result


def test_require_fact_batch_partial():
    @actguard.enforce([actguard.RequireFact("ids", "item_id")])
    def delete_items(ids: list):
        return "deleted"

    with actguard.session("sess-4"):
        mint("sess-4", "global", "item_id", "a", ttl=60)
        # "b" is NOT minted
        with pytest.raises(GuardError) as exc_info:
            delete_items(ids=["a", "b"])

    assert exc_info.value.code == "MISSING_FACT"
    assert exc_info.value.details["value"] == "b"


def test_scope_isolation():
    """Facts minted in session A are not visible in session B."""

    @actguard.enforce([actguard.RequireFact("order_id", "order_id")])
    def delete_order(order_id: str):
        return "deleted"

    with actguard.session("sess-a"):
        mint("sess-a", "global", "order_id", "o1", ttl=60)

    with actguard.session("sess-b"):
        with pytest.raises(GuardError) as exc_info:
            delete_order(order_id="o1")

    assert exc_info.value.code == "MISSING_FACT"


# ---------------------------------------------------------------------------
# Tests — Threshold
# ---------------------------------------------------------------------------


def test_threshold_exceeded():
    @actguard.enforce([actguard.Threshold("n", 10)])
    def bulk_op(n: int):
        return f"ok:{n}"

    with actguard.session("sess-thr-1"):
        with pytest.raises(GuardError) as exc_info:
            bulk_op(n=11)

    assert exc_info.value.code == "THRESHOLD_EXCEEDED"


def test_threshold_passes():
    @actguard.enforce([actguard.Threshold("n", 10)])
    def bulk_op(n: int):
        return f"ok:{n}"

    with actguard.session("sess-thr-2"):
        result = bulk_op(n=10)

    assert result == "ok:10"


# ---------------------------------------------------------------------------
# Tests — BlockRegex
# ---------------------------------------------------------------------------


def test_block_regex():
    @actguard.enforce([actguard.BlockRegex("q", r"\.\.")])
    def search(q: str):
        return f"results:{q}"

    with actguard.session("sess-re-1"):
        with pytest.raises(GuardError) as exc_info:
            search(q="../etc/passwd")

    assert exc_info.value.code == "PATTERN_BLOCKED"


# ---------------------------------------------------------------------------
# Tests — enforce no session
# ---------------------------------------------------------------------------


def test_enforce_no_session():
    @actguard.enforce([actguard.RequireFact("order_id", "order_id")])
    def delete_order(order_id: str):
        return "deleted"

    with pytest.raises(GuardError) as exc_info:
        delete_order(order_id="o1")

    assert exc_info.value.code == "NO_SESSION"


# ---------------------------------------------------------------------------
# Tests — GuardError.to_prompt
# ---------------------------------------------------------------------------


def test_guard_error_to_prompt_no_session():
    err = GuardError("NO_SESSION", "No session")
    prompt = err.to_prompt()
    assert "BLOCKED" in prompt
    assert "actguard.session()" in prompt


def test_guard_error_to_prompt_missing_fact():
    err = GuardError(
        "MISSING_FACT",
        "Value not proven",
        details={"kind": "order_id", "value": "o99"},
        fix_hint="Call list_orders first.",
    )
    prompt = err.to_prompt()
    assert "BLOCKED" in prompt
    assert "order_id" in prompt
    assert "o99" in prompt
    assert "Call list_orders first." in prompt


def test_guard_error_to_prompt_threshold():
    err = GuardError(
        "THRESHOLD_EXCEEDED",
        "Too many",
        fix_hint="Use n <= 10.",
    )
    prompt = err.to_prompt()
    assert "THRESHOLD_EXCEEDED" in prompt
    assert "Use n <= 10." in prompt
