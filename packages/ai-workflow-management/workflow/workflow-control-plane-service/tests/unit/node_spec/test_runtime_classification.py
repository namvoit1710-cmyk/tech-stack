"""Single-source node-runtime classification guards.

The engine's inline / pending / worker classification is DERIVED from the
domain ``NodeSpec.runtime`` field (``node_spec.registry.node_types_for_runtime``),
not hand-listed. These tests are the anti-drift guard:

1. the three runtimes partition every ``NodeType`` (none unclassified,
   none double-classified);
2. PROMPT and OUTPUT — the two node types that previously drifted OUT of the
   inline path and got mis-routed to the worker-dispatch branch — are
   classified as inline/builtin;
3. each handler's declared ``shape`` agrees with its spec's ``runtime``, so the
   layer-2 implementation and the layer-1 domain can never silently disagree.

If a new inline node type is added with a spec ``runtime="engine"`` and a
handler inheriting an inline base, every assertion below keeps passing with no
edit — that is the point. The tests fail only when the two sources drift.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.node_spec.model import NodeRuntime
from app.layer1_domain.node_spec.registry import get_spec, node_types_for_runtime
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.node_handlers.base import NodeHandlerShape
from app.layer2_application.orchestration.successor_processor import (
    BUILTIN_NODE_TYPES,
    PENDING_NODE_TYPES,
)

pytestmark = pytest.mark.unit

# Handler-shape -> domain runtime. Inline handlers (pure or async) run in the
# control-plane engine; pending handlers pause for the user; dispatching
# handlers hand off to a worker.
SHAPE_TO_RUNTIME = {
    NodeHandlerShape.PURE_INLINE: NodeRuntime.ENGINE,
    NodeHandlerShape.ASYNC_INLINE: NodeRuntime.ENGINE,
    NodeHandlerShape.PENDING_USER: NodeRuntime.PENDING,
    NodeHandlerShape.DISPATCH_EXTERNAL: NodeRuntime.WORKER,
}


def _handler_classes() -> dict[NodeType, type]:
    """Map every NodeType to its handler class (class-level ``shape`` only —
    no instantiation, so no handler dependencies are needed). A missing entry
    here trips ``test_every_node_type_has_a_handler``."""
    from app.layer2_application.node_handlers.approval_flow_handler import ApprovalFlowNodeHandler
    from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
    from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
    from app.layer2_application.node_handlers.data_edit_handler import DataEditNodeHandler
    from app.layer2_application.node_handlers.file_upload_handler import FileUploadNodeHandler
    from app.layer2_application.node_handlers.human_action_handler import HumanActionNodeHandler
    from app.layer2_application.node_handlers.input_handler import InputNodeHandler
    from app.layer2_application.node_handlers.loop_continue_handler import LoopContinueNodeHandler
    from app.layer2_application.node_handlers.loop_exit_handler import LoopExitNodeHandler
    from app.layer2_application.node_handlers.loop_handler import LoopNodeHandler
    from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
    from app.layer2_application.node_handlers.output_handler import OutputNodeHandler
    from app.layer2_application.node_handlers.parallel_handler import ParallelNodeHandler
    from app.layer2_application.node_handlers.prompt_handler import PromptNodeHandler
    from app.layer2_application.node_handlers.set_variable_handler import SetVariableHandler
    from app.layer2_application.node_handlers.switch_handler import SwitchNodeHandler
    from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
    from app.layer2_application.node_handlers.template_data_edit_handler import TemplateDataEditNodeHandler
    from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
    from app.layer2_application.node_handlers.wait_handler import WaitNodeHandler
    from app.layer2_application.node_handlers.workflow_handler import WorkflowNodeHandler

    return {
        NodeType.APPROVAL_FLOW: ApprovalFlowNodeHandler,
        NodeType.COMPUTE: ComputeNodeHandler,
        NodeType.CONDITION: ConditionNodeHandler,
        NodeType.DATA_EDIT: DataEditNodeHandler,
        NodeType.FILE_UPLOAD: FileUploadNodeHandler,
        NodeType.HUMAN_ACTION: HumanActionNodeHandler,
        NodeType.INPUT: InputNodeHandler,
        NodeType.LOOP: LoopNodeHandler,
        NodeType.LOOP_CONTINUE: LoopContinueNodeHandler,
        NodeType.LOOP_EXIT: LoopExitNodeHandler,
        NodeType.MERGE: MergeNodeHandler,
        NodeType.OUTPUT: OutputNodeHandler,
        NodeType.PARALLEL: ParallelNodeHandler,
        NodeType.PROMPT: PromptNodeHandler,
        NodeType.SET_VARIABLE: SetVariableHandler,
        NodeType.SWITCH: SwitchNodeHandler,
        NodeType.TASK: TaskNodeHandler,
        NodeType.TEMPLATE_DATA_EDIT: TemplateDataEditNodeHandler,
        NodeType.TRIGGER: TriggerNodeHandler,
        NodeType.WAIT: WaitNodeHandler,
        NodeType.WORKFLOW: WorkflowNodeHandler,
    }


def test_runtimes_partition_all_node_types() -> None:
    engine = node_types_for_runtime(NodeRuntime.ENGINE)
    pending = node_types_for_runtime(NodeRuntime.PENDING)
    worker = node_types_for_runtime(NodeRuntime.WORKER)

    # Cover every node type exactly once.
    assert engine | pending | worker == set(NodeType)
    assert engine & pending == frozenset()
    assert engine & worker == frozenset()
    assert pending & worker == frozenset()


def test_derived_sets_match_domain_runtime() -> None:
    # The orchestrator constants are the derived sets, nothing hand-listed.
    assert BUILTIN_NODE_TYPES == node_types_for_runtime(NodeRuntime.ENGINE)
    assert PENDING_NODE_TYPES == node_types_for_runtime(NodeRuntime.PENDING)


def test_prompt_and_output_are_inline_builtins() -> None:
    # Regression guard: both were missing from the old hand-listed frozenset,
    # which routed them down the worker-dispatch path (test_node did nothing).
    assert NodeType.PROMPT in BUILTIN_NODE_TYPES
    assert NodeType.OUTPUT in BUILTIN_NODE_TYPES
    assert NodeType.PROMPT not in PENDING_NODE_TYPES
    assert NodeType.OUTPUT not in PENDING_NODE_TYPES


def test_every_node_type_has_a_handler() -> None:
    assert set(_handler_classes()) == set(NodeType)


@pytest.mark.parametrize("node_type,handler_cls", list(_handler_classes().items()))
def test_handler_shape_matches_spec_runtime(node_type: NodeType, handler_cls: type) -> None:
    """The layer-2 handler ``shape`` and the layer-1 spec ``runtime`` must agree.

    This is what makes the classification truly single-source: if a handler is
    inline but its spec says ``worker`` (or vice versa), this fails before the
    mismatch can mis-route the node at runtime.
    """
    spec_runtime = NodeRuntime(get_spec(node_type).runtime)
    handler_runtime = SHAPE_TO_RUNTIME[handler_cls.shape]
    assert handler_runtime == spec_runtime, (
        f"{node_type.value}: handler shape {handler_cls.shape.value!r} implies "
        f"runtime {handler_runtime.value!r} but spec declares "
        f"{spec_runtime.value!r}"
    )
