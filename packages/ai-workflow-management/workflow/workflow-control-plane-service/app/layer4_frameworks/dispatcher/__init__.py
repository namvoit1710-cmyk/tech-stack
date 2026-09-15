"""Phase 2b — in-process Dispatcher shadow.

This package houses the Phase 2b DispatcherConsumer and its parity
comparator. In shadow mode the consumer runs alongside the engine's
inline ``_route_to_successors`` and records what fan-out events it
*would* have emitted. The comparator asserts both sides agree before
Phase 3a flips the authoritative bit and deletes the inline path.

See ``docs/design/phase-2-dispatcher-extraction.md`` for the full
design.
"""
from app.layer4_frameworks.dispatcher.dispatcher_consumer import (  # noqa: F401
    DispatcherConsumer,
    DispatcherTeeingPublisher,
)
from app.layer4_frameworks.dispatcher.dispatcher_parity_comparator import (  # noqa: F401
    DispatcherParityComparator,
    DispatcherParityRecorder,
)
