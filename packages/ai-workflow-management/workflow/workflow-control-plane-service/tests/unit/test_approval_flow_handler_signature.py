"""Regression: ApprovalFlowNodeHandler.handle must accept the engine's keyword dispatch.

Every dispatch site invokes handlers by KEYWORD — base.py ``invoke_handler``,
``orchestration_engine`` and ``executor_consumer`` all call
``handler.handle(node=..., run=..., workflow=..., topology=..., completed_task=...,
task_factory=...)``. ApprovalFlowNodeHandler had renamed those params to
``_topology`` / ``_completed_task`` / ``_task_factory`` (an "unused" convention),
which made keyword binding fail at runtime with
``ApprovalFlowNodeHandler.handle() got an unexpected keyword argument 'topology'``.
The existing handler tests call ``handle`` positionally, so they never caught it.

These bind the signatures with the engine's exact keyword names — no runtime
scaffolding needed — so the contract is locked for this handler.
"""
import inspect

from app.layer2_application.node_handlers.approval_flow_handler import (
    ApprovalFlowNodeHandler,
)
from app.layer2_application.node_handlers.base_node_handler import (
    BaseNodeHandler,
)

# The keyword names the engine dispatches with (see base.py:204,
# orchestration_engine.py:619, executor_consumer.py:313).
_ENGINE_KWARGS = dict(
    node=object(),
    run=object(),
    workflow=object(),
    topology=object(),
    completed_task=None,
    task_factory=object(),
)


def test_handle_accepts_engine_keyword_dispatch():
    sig = inspect.signature(ApprovalFlowNodeHandler.handle)
    # .bind raises TypeError("got an unexpected keyword argument 'topology'")
    # when a param is named `_topology` — the exact reported failure.
    sig.bind(object(), **_ENGINE_KWARGS)


def test_handle_param_names_match_abc_contract():
    abc = list(inspect.signature(BaseNodeHandler.handle).parameters)
    impl = list(inspect.signature(ApprovalFlowNodeHandler.handle).parameters)
    assert impl == abc, f"handle() params {impl} must match the ABC {abc} for keyword dispatch"
