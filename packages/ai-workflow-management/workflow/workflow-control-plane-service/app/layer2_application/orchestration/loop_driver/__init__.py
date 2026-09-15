"""Event-driven loop driver — Phase 5.2.1.

Owns the full iteration-orchestration flow for loops when
``EVENT_DRIVEN_LOOP`` is enabled. Absorbs the ``_ed_*`` methods that
previously lived on ``LoopNodeHandler`` and adds a new ``run_loop``
entry point that the engine calls DIRECTLY, bypassing the legacy
``_run_loop_iterations`` walker and fixing the event-ordering quirk
where ``LoopStarted`` used to publish after per-iteration body events.
"""

from app.layer2_application.orchestration.loop_driver.event_driven_loop_driver import (
    EventDrivenLoopDriver,
)

__all__ = ["EventDrivenLoopDriver"]
