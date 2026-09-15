"""Unit tests for RowPathResolver."""

from unittest.mock import AsyncMock

import pytest

from app.layer1_domain.entities.data_reference import add_artifact, build_file_ref
from app.layer2_application.data.row_path_resolver import (
    ArtifactNotInTreeError,
    PARENT_ROW_ID_COLUMN,
    ROW_ID_COLUMN,
    RowNotFoundError,
    RowPathResolver,
)


def _tree_orders_with_lineitems() -> dict:
    ref = build_file_ref(file_id="f_main")
    orders = add_artifact(ref, file_id="f_orders", name="orders", artifact_id="orders")
    add_artifact(
        orders, file_id="f_li", name="line_items",
        artifact_id="orders.line_items",
        parent_row_key_column=PARENT_ROW_ID_COLUMN,
    )
    return ref


def _fake_client(responses: list[dict]):
    """Build a mock IFileServiceClient whose query_file_data returns each
    response in turn."""
    client = AsyncMock()
    client.query_file_data.side_effect = responses
    return client


# ---------------------------------------------------------------------------
# Single-hop happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_hop_resolves_row_id():
    tree = _tree_orders_with_lineitems()
    client = _fake_client([{"data": [{ROW_ID_COLUMN: "r_1"}]}])
    resolver = RowPathResolver(client)

    result = await resolver.resolve(
        tree,
        [{"artifact_id": "orders", "row_index": 3, "filter": "$filter=region eq 'APAC'"}],
    )
    assert result == ["r_1"]


@pytest.mark.asyncio
async def test_single_hop_sends_correct_query_params():
    tree = _tree_orders_with_lineitems()
    client = _fake_client([{"data": [{ROW_ID_COLUMN: "r_1"}]}])
    resolver = RowPathResolver(client)

    await resolver.resolve(
        tree,
        [{
            "artifact_id": "orders", "row_index": 3,
            "filter": "$filter=region eq 'APAC'&$orderby=sku asc",
        }],
    )

    client.query_file_data.assert_awaited_once()
    file_id, params = client.query_file_data.await_args.args
    assert file_id == "f_orders"
    assert params["$filter"] == "region eq 'APAC'"
    assert params["$orderby"] == "sku asc"
    assert params["$skip"] == "3"
    assert params["$top"] == "1"
    assert params["$select"] == ROW_ID_COLUMN


# ---------------------------------------------------------------------------
# Nested hops: parent scoping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nested_hop_adds_parent_row_id_scope():
    tree = _tree_orders_with_lineitems()
    client = _fake_client([
        {"data": [{ROW_ID_COLUMN: "r_orders_3"}]},
        {"data": [{ROW_ID_COLUMN: "r_li_0"}]},
    ])
    resolver = RowPathResolver(client)

    result = await resolver.resolve(
        tree,
        [
            {"artifact_id": "orders", "row_index": 3, "filter": ""},
            {"artifact_id": "orders.line_items", "row_index": 0, "filter": ""},
        ],
    )

    assert result == ["r_orders_3", "r_li_0"]
    # Second call must scope by parent __row_id.
    second_params = client.query_file_data.await_args_list[1].args[1]
    assert second_params["$filter"] == f"{PARENT_ROW_ID_COLUMN} eq 'r_orders_3'"
    assert second_params["$skip"] == "0"
    assert second_params["$top"] == "1"


@pytest.mark.asyncio
async def test_nested_hop_combines_parent_scope_with_existing_filter():
    tree = _tree_orders_with_lineitems()
    client = _fake_client([
        {"data": [{ROW_ID_COLUMN: "r_parent"}]},
        {"data": [{ROW_ID_COLUMN: "r_child"}]},
    ])
    resolver = RowPathResolver(client)

    await resolver.resolve(
        tree,
        [
            {"artifact_id": "orders", "row_index": 0, "filter": ""},
            {
                "artifact_id": "orders.line_items",
                "row_index": 2,
                "filter": "$filter=price lt 10",
            },
        ],
    )

    second_params = client.query_file_data.await_args_list[1].args[1]
    # Both clauses present, AND-combined.
    assert "__parent_row_id eq 'r_parent'" in second_params["$filter"]
    assert "price lt 10" in second_params["$filter"]
    assert " and " in second_params["$filter"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_raises_when_artifact_not_in_tree():
    tree = _tree_orders_with_lineitems()
    client = _fake_client([])
    resolver = RowPathResolver(client)

    with pytest.raises(ArtifactNotInTreeError) as err:
        await resolver.resolve(
            tree,
            [{"artifact_id": "unknown", "row_index": 0, "filter": ""}],
        )
    assert err.value.hop_index == 0
    assert err.value.artifact_id == "unknown"


@pytest.mark.asyncio
async def test_raises_when_row_index_out_of_range():
    tree = _tree_orders_with_lineitems()
    client = _fake_client([{"data": []}])
    resolver = RowPathResolver(client)

    with pytest.raises(RowNotFoundError) as err:
        await resolver.resolve(
            tree,
            [{"artifact_id": "orders", "row_index": 999, "filter": ""}],
        )
    assert err.value.hop_index == 0
    assert err.value.row_index == 999


@pytest.mark.asyncio
async def test_raises_when_row_missing_row_id_column():
    tree = _tree_orders_with_lineitems()
    client = _fake_client([{"data": [{"sku": "X1"}]}])  # no __row_id
    resolver = RowPathResolver(client)

    with pytest.raises(RowNotFoundError) as err:
        await resolver.resolve(
            tree,
            [{"artifact_id": "orders", "row_index": 0, "filter": ""}],
        )
    assert "missing" in err.value.reason


@pytest.mark.asyncio
async def test_raises_at_correct_hop_when_mid_chain_fails():
    tree = _tree_orders_with_lineitems()
    client = _fake_client([
        {"data": [{ROW_ID_COLUMN: "r_orders_3"}]},
        {"data": []},  # nested hop fails
    ])
    resolver = RowPathResolver(client)

    with pytest.raises(RowNotFoundError) as err:
        await resolver.resolve(
            tree,
            [
                {"artifact_id": "orders", "row_index": 3, "filter": ""},
                {"artifact_id": "orders.line_items", "row_index": 0, "filter": ""},
            ],
        )
    assert err.value.hop_index == 1
    assert err.value.artifact_id == "orders.line_items"


# ---------------------------------------------------------------------------
# Data-shape tolerance (data vs value)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_accepts_odata_value_key_as_fallback():
    tree = _tree_orders_with_lineitems()
    # File service returns OData v4 shape with `value` instead of `data`.
    client = _fake_client([{"value": [{ROW_ID_COLUMN: "r_1"}]}])
    resolver = RowPathResolver(client)

    result = await resolver.resolve(
        tree,
        [{"artifact_id": "orders", "row_index": 0, "filter": ""}],
    )
    assert result == ["r_1"]
