"""Dispatching handler base — typed shape marker.

``DispatchingHandlerBase`` is the family marker for handlers that hand
work off to a worker via the messaging layer. Today this is only
:class:`TaskNodeHandler`, but the marker is kept distinct from
``AsyncInlineHandlerBase`` so the ``node_handler_duration_seconds``
metric carries the correct ``handler_shape`` label and so future
worker-style handlers can opt in by inheritance alone.
"""

from __future__ import annotations

from abc import ABC
from typing import ClassVar

from app.layer2_application.node_handlers.base import NodeHandlerShape
from app.layer2_application.node_handlers.base_node_handler import BaseNodeHandler


class DispatchingHandlerBase(BaseNodeHandler, ABC):
    """Hands work off to a worker via the messaging layer.

    Sets ``shape = DISPATCH_EXTERNAL``. Concrete subclass: :class:`TaskNodeHandler`.
    """

    shape: ClassVar[NodeHandlerShape] = NodeHandlerShape.DISPATCH_EXTERNAL


__all__ = ["DispatchingHandlerBase"]
