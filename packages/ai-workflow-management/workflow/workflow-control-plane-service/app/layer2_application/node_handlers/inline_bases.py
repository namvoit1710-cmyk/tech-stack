"""Inline handler base classes — typed shape markers.

``PureInlineHandlerBase`` and ``AsyncInlineHandlerBase`` add no
behaviour beyond fixing ``shape`` as a ClassVar. They exist so
test code and ``invoke_handler`` can pattern-match on the family
(``isinstance(handler, PureInlineHandlerBase)``), and so a new pure-
inline handler doesn't have to remember to set ``shape =
NodeHandlerShape.PURE_INLINE`` on the class body.
"""

from __future__ import annotations

from abc import ABC
from typing import ClassVar

from app.layer2_application.node_handlers.base import NodeHandlerShape
from app.layer2_application.node_handlers.base_node_handler import BaseNodeHandler


class PureInlineHandlerBase(BaseNodeHandler, ABC):
    """No resolver calls, no I/O.

    Examples: TRIGGER, MERGE, LOOP_EXIT, LOOP_CONTINUE.
    """

    shape: ClassVar[NodeHandlerShape] = NodeHandlerShape.PURE_INLINE


class AsyncInlineHandlerBase(BaseNodeHandler, ABC):
    """May await the variable resolver (which may hit the file service).

    Examples: COMPUTE, OUTPUT, SET_VARIABLE, APPROVAL_FLOW, LOOP,
    PARALLEL, WORKFLOW. CONDITION/SWITCH inherit from the routing-
    specialised subclass ``RoutingHandlerBase``.
    """

    shape: ClassVar[NodeHandlerShape] = NodeHandlerShape.ASYNC_INLINE


__all__ = ["PureInlineHandlerBase", "AsyncInlineHandlerBase"]
