"""Tests for the @prove decorator."""

import pytest

import actguard
from actguard.exceptions import GuardError
from actguard.tools._facts import _FACTS, exists

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class Obj:
    def __init__(self, id):
        self.id = id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_prove_dict_extraction():
    @actguard.prove(kind="order_id", extract="id")
    def get_order():
        return {"id": 1}

    with actguard.session("sess-1"):
        result = get_order()

    assert result == {"id": 1}
    assert exists("sess-1", "global", "order_id", "1")


def test_prove_attr_extraction():
    @actguard.prove(kind="user_id", extract="id")
    def get_user():
        return Obj(id=42)

    with actguard.session("sess-2"):
        get_user()

    assert exists("sess-2", "global", "user_id", "42")


def test_prove_list_of_dicts():
    @actguard.prove(kind="item_id", extract="id")
    def list_items():
        return [{"id": "a"}, {"id": "b"}, {"id": "c"}]

    with actguard.session("sess-3"):
        list_items()

    for v in ("a", "b", "c"):
        assert exists("sess-3", "global", "item_id", v)


def test_prove_callable_extract():
    @actguard.prove(kind="order_id", extract=lambda r: [r["id"]])
    def get_order():
        return {"id": "x99"}

    with actguard.session("sess-4"):
        get_order()

    assert exists("sess-4", "global", "order_id", "x99")


def test_prove_too_many_block():
    @actguard.prove(kind="item_id", extract="id", max_items=2, on_too_many="block")
    def list_items():
        return [{"id": str(i)} for i in range(3)]

    with actguard.session("sess-5"):
        with pytest.raises(GuardError) as exc_info:
            list_items()

    assert exc_info.value.code == "TOO_MANY_RESULTS"
    assert len(_FACTS) == 0  # nothing minted


def test_prove_too_many_truncate():
    @actguard.prove(kind="item_id", extract="id", max_items=2, on_too_many="truncate")
    def list_items():
        return [{"id": str(i)} for i in range(5)]

    with actguard.session("sess-6"):
        list_items()

    # Only first 2 minted
    assert exists("sess-6", "global", "item_id", "0")
    assert exists("sess-6", "global", "item_id", "1")
    assert not exists("sess-6", "global", "item_id", "2")


def test_prove_no_session():
    @actguard.prove(kind="order_id", extract="id")
    def get_order():
        return {"id": 1}

    with pytest.raises(GuardError) as exc_info:
        get_order()

    assert exc_info.value.code == "NO_SESSION"
    assert "actguard.session()" in exc_info.value.to_prompt()


@pytest.mark.asyncio
async def test_prove_async():
    @actguard.prove(kind="product_id", extract="id")
    async def get_product():
        return {"id": "p1"}

    async with actguard.session("sess-async"):
        result = await get_product()

    assert result == {"id": "p1"}
    assert exists("sess-async", "global", "product_id", "p1")
