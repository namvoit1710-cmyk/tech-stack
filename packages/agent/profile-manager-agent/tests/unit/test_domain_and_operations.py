"""Unit specs for pure layer1 domain + layer2 operation helpers (no mocks needed).

Targets:
- app/layer1_domain/entities/odata_query.py (ODataQuery)
- app/layer1_domain/entities/exceptions.py (domain exceptions)
- app/layer1_domain/services/operation_registry.py (load_operations)
- app/layer2_application/features/execute_agent/operation_loader.py (build_operations_prompt)
- app/layer1_domain/operations/user_query.py (the OPERATION spec, verified structure)
"""
import pytest

from app.layer1_domain.entities.odata_query import ODataQuery
from app.layer1_domain.entities.exceptions import (
    NotFoundException,
    UnauthorizedException,
    ForbiddenException,
    ConflictException,
)
from app.layer2_application.features.execute_agent.operation_loader import (
    build_operations_prompt,
)
from app.layer4_frameworks.catalog.operation_catalog import OperationCatalog
from app.layer4_frameworks.prompt.operations.user_query import OPERATION


# --------------------------------------------------------------------------- #
# ODataQuery entity
# --------------------------------------------------------------------------- #

# happy path: all fields set
def test_odata_query_all_fields():
    q = ODataQuery(
        filter="name eq 'A'",
        orderby="name desc",
        top=10,
        skip=5,
        select="id,name",
        count=True,
    )
    assert q.filter == "name eq 'A'"
    assert q.orderby == "name desc"
    assert q.top == 10
    assert q.skip == 5
    assert q.select == "id,name"
    assert q.count is True


# boundary / edge: constructed with no args -> all None (source defaults)
def test_odata_query_defaults_all_none():
    q = ODataQuery()
    assert (q.filter, q.orderby, q.top, q.skip, q.select, q.count) == (
        None, None, None, None, None, None,
    )


# invalid input tolerance: unpacking a partial dict only sets provided keys
def test_odata_query_from_partial_dict():
    q = ODataQuery(**{"filter": "x eq 1", "top": 3})
    assert q.filter == "x eq 1"
    assert q.top == 3
    assert q.skip is None


# --------------------------------------------------------------------------- #
# Domain exceptions — .detail composition
# --------------------------------------------------------------------------- #

# happy path + default message
def test_not_found_exception_detail_composition():
    assert NotFoundException("User").detail == "User Not Found"
    assert NotFoundException("User", "missing").detail == "User missing"


def test_unauthorized_exception_detail():
    assert UnauthorizedException().detail == "Unauthorized"
    assert UnauthorizedException("bad token").detail == "bad token"


def test_forbidden_exception_detail():
    assert ForbiddenException().detail == "Forbidden"
    assert ForbiddenException("nope").detail == "nope"


def test_conflict_exception_detail_composition():
    assert ConflictException("User").detail == "User Conflict"
    assert ConflictException("User", "exists").detail == "User exists"


# edge: these are real Exceptions and can be raised/caught
def test_exceptions_are_raisable():
    with pytest.raises(UnauthorizedException):
        raise UnauthorizedException("x")


# --------------------------------------------------------------------------- #
# build_operations_prompt (layer2 helper)
# --------------------------------------------------------------------------- #

# happy path: single op -> scope/description/operations rendered
def test_build_operations_prompt_single_op():
    ops = [{"scope": "user", "description": "desc", "operations": [{"type": "filter"}]}]
    text = build_operations_prompt(ops)
    assert "Scope: user" in text
    assert "Description: desc" in text
    assert "filter" in text


# boundary: empty list -> empty string (source starts text="")
def test_build_operations_prompt_empty_list():
    assert build_operations_prompt([]) == ""


# edge: multiple ops -> each contributes a block
def test_build_operations_prompt_multiple_ops():
    ops = [
        {"scope": "user", "description": "d1", "operations": []},
        {"scope": "role", "description": "d2", "operations": []},
    ]
    text = build_operations_prompt(ops)
    assert "Scope: user" in text and "Scope: role" in text


# invalid input: op missing a required key -> KeyError (grounded in dict access)
def test_build_operations_prompt_missing_key_raises():
    with pytest.raises(KeyError):
        build_operations_prompt([{"scope": "user"}])  # no 'description'


# --------------------------------------------------------------------------- #
# OperationCatalog (layer4) + the user_query OPERATION asset
# --------------------------------------------------------------------------- #

# happy path: the catalog lists the user-scoped OPERATION (no dynamic import, P4)
def test_operation_catalog_lists_user_scope():
    ops = OperationCatalog().list_operations()
    assert any(op.get("scope") == "user" for op in ops)


# verified structure: the shipped OPERATION spec advertises the 5 op types
def test_user_query_operation_spec_shape():
    assert OPERATION["scope"] == "user"
    types = {o["type"] for o in OPERATION["operations"]}
    assert types == {"filter", "orderby", "pagination", "select", "count"}
