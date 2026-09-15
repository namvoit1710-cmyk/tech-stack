"""Unit tests for the node-handler ABC tree.

Covers the structural invariants of ``BaseNodeHandler``,
``PendingHandlerBase`` and ``RoutingHandlerBase`` — that abstract
methods make the class non-instantiable and that subclasses inherit
the right ``shape`` ClassVar.
"""

from __future__ import annotations

import pytest

from app.layer2_application.node_handlers.base import NodeHandlerShape
from app.layer2_application.node_handlers.base_node_handler import (
    BaseNodeHandler,
    RoutingDecision,
)
from app.layer2_application.node_handlers.inline_bases import (
    AsyncInlineHandlerBase,
    PureInlineHandlerBase,
)
from app.layer2_application.node_handlers.dispatching_base import (
    DispatchingHandlerBase,
)
from app.layer2_application.node_handlers.pending_base import PendingHandlerBase
from app.layer2_application.node_handlers.routing_base import RoutingHandlerBase


def test_base_handler_is_abstract():
    with pytest.raises(TypeError):
        BaseNodeHandler()  # type: ignore[abstract]


def test_routing_handler_base_is_abstract():
    with pytest.raises(TypeError):
        RoutingHandlerBase()  # type: ignore[abstract]


def test_pending_handler_base_is_abstract():
    with pytest.raises(TypeError):
        PendingHandlerBase(variable_resolver=None)  # type: ignore[abstract]


def test_inline_marker_bases_are_abstract():
    with pytest.raises(TypeError):
        PureInlineHandlerBase()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        AsyncInlineHandlerBase()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        DispatchingHandlerBase()  # type: ignore[abstract]


def test_shape_classvars_match_family():
    assert BaseNodeHandler.shape == NodeHandlerShape.UNKNOWN
    assert PureInlineHandlerBase.shape == NodeHandlerShape.PURE_INLINE
    assert AsyncInlineHandlerBase.shape == NodeHandlerShape.ASYNC_INLINE
    assert RoutingHandlerBase.shape == NodeHandlerShape.ASYNC_INLINE
    assert DispatchingHandlerBase.shape == NodeHandlerShape.DISPATCH_EXTERNAL
    assert PendingHandlerBase.shape == NodeHandlerShape.PENDING_USER


def test_routing_decision_dataclass_defaults():
    decision = RoutingDecision(matched_branch="then", matched_port_id="")
    assert decision.matched_branch == "then"
    assert decision.completion_payload == {}
    # Default edge_accepts must be callable and return None
    assert decision.edge_accepts(object()) is None


def test_base_handler_get_schema_provider_returns_none_by_default():
    class _Stub(BaseNodeHandler):
        async def handle(self, *a, **kw):  # pragma: no cover — just satisfying ABC
            ...

    assert _Stub().get_schema_provider() is None
