"""SA-1815 — Aggregate / Reduce node (`mapping_aggregate`) + AggregateEngine.

N source items -> grouped rows: group_by (0+ keys) + aggregations
(sum/count/min/max/avg/collect/join). Net-new capability (previously only a
COMPUTE code node could do this).
"""
import pytest

from app.layer4_frameworks.providers.aggregate_engine import AggregateEngine
from app import node_types as nt


@pytest.fixture
def eng():
    return AggregateEngine()


ORDERS = [
    {"region": "NA", "amount": 10, "sku": "a"},
    {"region": "NA", "amount": 5, "sku": "b"},
    {"region": "EU", "amount": 7, "sku": "c"},
]


# =========================================================================
# Engine — no group_by (reduce all)
# =========================================================================


class TestNoGroupBy:
    def test_single_overall_row(self, eng):
        r = eng.aggregate(ORDERS, [], [
            {"op": "sum", "field": "amount", "target": "total"},
            {"op": "count", "target": "n"},
        ])
        assert r["result"] == [{"total": 22, "n": 3}]
        assert r["group_count"] == 1

    def test_avg_min_max_collect_join(self, eng):
        r = eng.aggregate(ORDERS, [], [
            {"op": "avg", "field": "amount", "target": "avg"},
            {"op": "min", "field": "amount", "target": "lo"},
            {"op": "max", "field": "amount", "target": "hi"},
            {"op": "collect", "field": "sku", "target": "skus"},
            {"op": "join", "field": "sku", "target": "sku_csv", "separator": "|"},
        ])
        row = r["result"][0]
        assert row["avg"] == pytest.approx(22 / 3)
        assert row["lo"] == 5 and row["hi"] == 10
        assert row["skus"] == ["a", "b", "c"]
        assert row["sku_csv"] == "a|b|c"

    def test_empty_items(self, eng):
        r = eng.aggregate([], [], [
            {"op": "sum", "field": "amount", "target": "total"},
            {"op": "count", "target": "n"},
            {"op": "avg", "field": "amount", "target": "avg"},
        ])
        assert r["result"] == [{"total": 0, "n": 0, "avg": None}]


# =========================================================================
# Engine — group_by
# =========================================================================


class TestGroupBy:
    def test_group_by_one_key(self, eng):
        r = eng.aggregate(ORDERS, ["region"], [
            {"op": "sum", "field": "amount", "target": "total"},
            {"op": "count", "target": "n"},
        ])
        assert r["result"] == [
            {"region": "NA", "total": 15, "n": 2},
            {"region": "EU", "total": 7, "n": 1},
        ]
        assert r["group_count"] == 2

    def test_group_by_two_keys(self, eng):
        items = [
            {"r": "NA", "t": "x", "v": 1},
            {"r": "NA", "t": "x", "v": 2},
            {"r": "NA", "t": "y", "v": 4},
        ]
        r = eng.aggregate(items, ["r", "t"], [{"op": "sum", "field": "v", "target": "sum"}])
        assert r["result"] == [
            {"r": "NA", "t": "x", "sum": 3},
            {"r": "NA", "t": "y", "sum": 4},
        ]

    def test_group_key_missing_becomes_none(self, eng):
        items = [{"amount": 1}, {"region": "EU", "amount": 2}]
        r = eng.aggregate(items, ["region"], [{"op": "sum", "field": "amount", "target": "s"}])
        assert {"region": None, "s": 1} in r["result"]
        assert {"region": "EU", "s": 2} in r["result"]


# =========================================================================
# Engine — value handling + edges
# =========================================================================


class TestValueHandling:
    def test_sum_skips_non_numeric_and_missing(self, eng):
        items = [{"a": 5}, {"a": "x"}, {"b": 9}, {"a": 3}]
        r = eng.aggregate(items, [], [{"op": "sum", "field": "a", "target": "s"}])
        assert r["result"][0]["s"] == 8   # 5 + 3; "x" and missing skipped

    def test_min_max_avg_none_when_no_numerics(self, eng):
        items = [{"a": "x"}, {"b": 1}]
        r = eng.aggregate(items, [], [
            {"op": "min", "field": "a", "target": "lo"},
            {"op": "avg", "field": "a", "target": "av"},
        ])
        assert r["result"][0] == {"lo": None, "av": None}

    def test_count_ignores_field(self, eng):
        r = eng.aggregate(ORDERS, [], [{"op": "count", "target": "n"}])
        assert r["result"][0]["n"] == 3

    def test_target_defaults_to_field_or_op(self, eng):
        r = eng.aggregate(ORDERS, [], [{"op": "sum", "field": "amount"}])
        assert r["result"][0]["amount"] == 22   # target defaulted to field
        r2 = eng.aggregate(ORDERS, [], [{"op": "count"}])
        assert r2["result"][0]["count"] == 3     # target defaulted to op

    def test_unknown_op_recorded_not_raised(self, eng):
        r = eng.aggregate(ORDERS, [], [
            {"op": "median", "field": "amount", "target": "m"},
            {"op": "count", "target": "n"},
        ])
        assert r["result"][0].get("n") == 3       # good agg still applied
        assert "m" not in r["result"][0]
        assert len(r["errors"]) == 1

    def test_join_default_separator(self, eng):
        r = eng.aggregate(ORDERS, [], [{"op": "join", "field": "sku", "target": "j"}])
        assert r["result"][0]["j"] == "a,b,c"

    def test_nested_field_path(self, eng):
        items = [{"o": {"amt": 2}}, {"o": {"amt": 3}}]
        r = eng.aggregate(items, [], [{"op": "sum", "field": "o.amt", "target": "s"}])
        assert r["result"][0]["s"] == 5

    def test_non_list_source_raises(self, eng):
        with pytest.raises(ValueError):
            eng.aggregate({"not": "a list"}, [], [])

    def test_booleans_not_summed_as_numbers(self, eng):
        items = [{"a": True}, {"a": 2}]
        r = eng.aggregate(items, [], [{"op": "sum", "field": "a", "target": "s"}])
        assert r["result"][0]["s"] == 2   # True skipped (not treated as 1)


# =========================================================================
# Node registration + handler
# =========================================================================


class TestAggregateNode:
    def test_four_node_types(self):
        wts = [d.worker_type for d in nt.NODE_TYPES]
        assert wts == ["mapping_data", "array_transform", "mapping_iterate", "mapping_aggregate"]

    def test_input_schema_keys(self):
        keys = {f["key"] for f in nt.AGGREGATE_NODE_DEF.input_schema}
        assert {"source_data", "source_array_path", "group_by", "aggregations", "strict_mode"} <= keys

    def test_output_schema_keys(self):
        keys = {f["key"] for f in nt.AGGREGATE_NODE_DEF.output_schema}
        assert {"result", "group_count", "errors"} <= keys

    def test_handler_group_by_end_to_end(self):
        out = nt._aggregate_handler({
            "source_data": {"orders": ORDERS},
            "source_array_path": "orders",
            "group_by": [{"field": "region"}],
            "aggregations": [
                {"op": "sum", "field": "amount", "target": "total"},
                {"op": "count", "target": "n"},
            ],
        }, {})
        assert out["result"] == [
            {"region": "NA", "total": 15, "n": 2},
            {"region": "EU", "total": 7, "n": 1},
        ]
        assert out["group_count"] == 2

    def test_handler_source_data_json_string(self):
        import json
        out = nt._aggregate_handler({
            "source_data": json.dumps({"orders": ORDERS}),
            "source_array_path": "orders",
            "aggregations": [{"op": "count", "target": "n"}],
        }, {})
        assert out["result"][0]["n"] == 3

    def test_handler_missing_array_yields_empty(self):
        out = nt._aggregate_handler({
            "source_data": {"other": 1},
            "source_array_path": "orders",
            "aggregations": [{"op": "count", "target": "n"}],
        }, {})
        # missing array -> [] -> one overall row over zero items
        assert out["result"] == [{"n": 0}]

    def test_handler_non_list_array_raises(self):
        with pytest.raises(ValueError):
            nt._aggregate_handler({
                "source_data": {"orders": {"not": "list"}},
                "source_array_path": "orders",
                "aggregations": [{"op": "count", "target": "n"}],
            }, {})

    def test_handler_source_data_not_object_raises(self):
        with pytest.raises(ValueError):
            nt._aggregate_handler({"source_data": [1, 2, 3], "source_array_path": "x"}, {})
