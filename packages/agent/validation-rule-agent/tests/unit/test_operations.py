"""Unit tests for layer1 operation definitions + the loader/prompt builder.

Covers: planner_worker._load_operations (pkgutil discovery),
operation_loader.build_operations_prompt (pure formatter), and the three
OPERATION dicts (column_expression / column_required / column_unique).
All pure — no mocks. Grounded in the source files.
"""

from __future__ import annotations

import json

import pytest

from app.layer4_frameworks.planner_worker import _load_operations as load_operations
from app.layer2_application.features.execute_agent.operation_loader import (
    build_operations_prompt,
)
from app.layer1_domain.operations.column_expression import OPERATION as EXPR_OP
from app.layer1_domain.operations.column_required import OPERATION as REQ_OP
from app.layer1_domain.operations.column_unique import OPERATION as UNIQ_OP


# --------------------------------------------------------------------------
# load_operations — happy path (discovers the 3 OPERATION-bearing modules)
# --------------------------------------------------------------------------
def test_load_operations_discovers_all_three():
    ops = load_operations()
    types = {op["type"] for op in ops}
    # exactly the three concrete operation modules expose OPERATION
    assert {"expression", "required", "unique"} <= types
    assert len(ops) >= 3


def test_load_operations_entries_are_well_formed():
    ops = load_operations()
    for op in ops:
        assert op["scope"] == "column"
        assert isinstance(op["description"], str) and op["description"]
        assert "example" in op


# --------------------------------------------------------------------------
# OPERATION dicts — structural contract (edge: required keys present)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("op,expected_type", [
    (EXPR_OP, "expression"),
    (REQ_OP, "required"),
    (UNIQ_OP, "unique"),
])
def test_operation_dict_shape(op, expected_type):
    assert op["scope"] == "column"
    assert op["type"] == expected_type
    assert op["example"]["type"] == expected_type
    assert "columns" in op["example"]["params"]


def test_expression_example_carries_polars_expression():
    # expression op is the only one whose example params include an expression
    assert "expression" in EXPR_OP["example"]["params"]
    assert "pl.col" in EXPR_OP["example"]["params"]["expression"]


# --------------------------------------------------------------------------
# build_operations_prompt — happy path
# --------------------------------------------------------------------------
def test_build_prompt_includes_type_scope_and_example():
    ops = [REQ_OP]
    text = build_operations_prompt(ops)

    assert "Operation Type: required" in text
    assert "Scope: column" in text
    # the example is embedded as pretty-printed JSON
    assert json.dumps(REQ_OP["example"], indent=2) in text


def test_build_prompt_concatenates_multiple_operations():
    text = build_operations_prompt([REQ_OP, UNIQ_OP])
    assert "Operation Type: required" in text
    assert "Operation Type: unique" in text


# --------------------------------------------------------------------------
# boundary: empty operations list -> empty prompt string
# --------------------------------------------------------------------------
def test_build_prompt_empty_list_returns_empty_string():
    assert build_operations_prompt([]) == ""


# --------------------------------------------------------------------------
# invalid input / failure path: op missing required keys raises KeyError
# --------------------------------------------------------------------------
def test_build_prompt_missing_key_raises_keyerror():
    with pytest.raises(KeyError):
        build_operations_prompt([{"type": "x"}])  # no 'scope'/'description'/'example'
