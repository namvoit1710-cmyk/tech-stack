"""The ``output_metadata`` contract must match what the handlers actually write.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §18.
Contract: ``app/layer2_application/data/task_metadata.py``.

A contract nobody executes is a comment, and comments in this codebase have
already been measured wrong: docstrings that enumerate go stale (four clusters
proven), and §6 of the spec described this very column as read *"only for
``error_code``"* when thirteen sites read it.

So the enumeration is not trusted — it is CALLED. Each PENDING handler's real
``build_output_metadata`` runs, and its key set is compared to the table. A
handler that grows a key without declaring it fails here instead of silently
widening a contract that other code narrows against.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects import NodeType
from app.layer2_application.data.task_metadata import (
    ALLOWED_KEYS,
    DISPATCH_KEYS,
    DISPATCH_KEYS_BY_NODE_TYPE,
    REASON_VALUES,
    TASK_METADATA_MAX_BYTES,
    TERMINAL_KEYS,
    narrow,
)
from app.layer2_application.node_handlers.data_edit_handler import DataEditNodeHandler
from app.layer2_application.node_handlers.file_upload_handler import (
    FileUploadNodeHandler,
)
from app.layer2_application.node_handlers.human_action_handler import (
    HumanActionNodeHandler,
)
from app.layer2_application.node_handlers.input_handler import InputNodeHandler
from app.layer2_application.node_handlers.wait_handler import WaitNodeHandler
from app.layer2_application.resolve.variable_resolver import (
    TraversalResolverStrategy,
    VariableResolver,
)


def _resolver() -> VariableResolver:
    return VariableResolver(strategies=[TraversalResolverStrategy()])


def _run() -> WorkflowRun:
    return WorkflowRun(id="r1", workflow_id="wf", workflow_version="1",
                       context=RunContext())


def _node(node_type: NodeType) -> WorkflowNode:
    return WorkflowNode(id="n1", name="N", node_type=node_type, config={})


# (declared node_type, handler factory, NodeType for the node fixture)
_HANDLERS = [
    ("INPUT", lambda: InputNodeHandler(_resolver()), NodeType.INPUT),
    ("HUMAN_ACTION", lambda: HumanActionNodeHandler(_resolver()),
     NodeType.HUMAN_ACTION),
    ("FILE_UPLOAD", lambda: FileUploadNodeHandler(_resolver()),
     NodeType.FILE_UPLOAD),
    ("WAIT", lambda: WaitNodeHandler(_resolver()), NodeType.WAIT),
    ("DATA_EDIT", lambda: DataEditNodeHandler(_resolver()), NodeType.DATA_EDIT),
]


class TestTheDeclaredContractIsWhatTheHandlersBuild:
    @pytest.mark.parametrize("declared,factory,node_type", _HANDLERS)
    def test_handler_keys_are_declared(self, declared, factory, node_type):
        meta = factory().build_output_metadata(
            node=_node(node_type), run=_run(), resolved_input={},
            prefilled={}, input_schema=[], ctx={},
        )
        undeclared = set(meta) - DISPATCH_KEYS_BY_NODE_TYPE[declared]
        assert not undeclared, (
            f"{declared} writes {sorted(undeclared)}, which task_metadata.py does "
            f"not declare. Add them there (and check nothing narrows them away) "
            f"— do not delete this assertion."
        )

    @pytest.mark.parametrize("declared,factory,node_type", _HANDLERS)
    def test_the_declaration_is_not_wider_than_reality(self, declared, factory,
                                                       node_type):
        """The other direction: a key declared but no longer written is a stale
        contract, and stale enumerations are what this file exists to prevent."""
        meta = factory().build_output_metadata(
            node=_node(node_type), run=_run(), resolved_input={},
            prefilled={}, input_schema=[], ctx={},
        )
        stale = DISPATCH_KEYS_BY_NODE_TYPE[declared] - set(meta)
        assert not stale, (
            f"task_metadata.py declares {sorted(stale)} for {declared} but the "
            f"handler no longer writes them"
        )

    @pytest.mark.parametrize("declared,factory,node_type", _HANDLERS)
    def test_node_type_matches_its_own_key(self, declared, factory, node_type):
        """Each handler stamps ``node_type`` — the discriminator every reader
        uses to know which half of the contract it is holding."""
        meta = factory().build_output_metadata(
            node=_node(node_type), run=_run(), resolved_input={},
            prefilled={}, input_schema=[], ctx={},
        )
        assert meta["node_type"] == declared

    def test_template_data_edit_extends_data_edit(self):
        """It calls ``super().build_output_metadata`` then adds two keys, so the
        declaration must be a strict superset — not a hand-copied list."""
        extra = (DISPATCH_KEYS_BY_NODE_TYPE["TEMPLATE_DATA_EDIT"]
                 - DISPATCH_KEYS_BY_NODE_TYPE["DATA_EDIT"])
        assert extra == {"template_id", "business_data_model_id"}
        assert (DISPATCH_KEYS_BY_NODE_TYPE["DATA_EDIT"]
                < DISPATCH_KEYS_BY_NODE_TYPE["TEMPLATE_DATA_EDIT"])


class TestTheTerminalHalf:
    def test_every_terminal_key_is_allowed(self):
        assert TERMINAL_KEYS <= ALLOWED_KEYS

    def test_the_run_deciding_keys_are_present(self):
        """``skipped`` decides whether a FAILED task fails the RUN
        (``workflow_run.py`` ``decide_completion``); ``reason`` carries
        ``parent_timeout``, which resume matches on by exact string."""
        assert {"skipped", "reason", "error_code"} <= TERMINAL_KEYS

    def test_parent_timeout_is_a_declared_reason(self):
        assert "parent_timeout" in REASON_VALUES

    def test_dispatch_and_terminal_do_not_overlap(self):
        """They are written by different paths for different node kinds. An
        overlap would make ``narrow`` ambiguous about which half it is keeping."""
        assert not (DISPATCH_KEYS & TERMINAL_KEYS)


class TestNarrowKeepsTheContractAndDropsTheEcho:
    def test_a_worker_payload_is_dropped_whole(self):
        """The measured shape: a ``get_comments`` completion carried 642,530
        chars of ``raw``/``comments`` and four bytes of ``error_code``."""
        meta = {
            "raw": {"body": "x" * 5000},
            "comments": [{"c": "y" * 200} for _ in range(50)],
            "issue_key": "SA-1",
            "success": True,
            "error_code": "",
        }
        r = narrow(meta)
        assert set(r.kept) == {"error_code"}
        assert set(r.dropped_keys) == {"raw", "comments", "issue_key", "success"}
        assert r.dropped_bytes > r.kept_bytes * 100

    def test_a_pending_contract_survives_intact(self):
        meta = {
            "node_type": "DATA_EDIT",
            "input_schema": [{"name": "a"}],
            "instruction": "edit",
            "prefilled": {},
            "readonly": True,
            "actions": [{"id": "ok"}],
            "settings": [],
            "query_params": {},
            "custom_data_update": False,
            "selectionMode": "multiple",
            "source_node_id": "up",
            "source_node_name": "Up",
            "source_file_id": "f1",
            "file_id": "f1",
            "source_columns": ["a", "b"],
            "source_row_count": 2,
            "artifact_key": "",
            "timeout_minutes": 60,
        }
        r = narrow(meta)
        assert r.kept == meta
        assert r.dropped_keys == []
        assert not r.changed

    def test_an_oversize_schema_is_kept_not_rejected(self):
        """§17.4: 113 of 204 real PENDING nodes exceed the 5000-char cap that was
        proposed, the largest at 23,468. The budget is 64 KB precisely so those
        dispatch."""
        big = [{"name": f"f{i}", "label": "q" * 60} for i in range(200)]
        r = narrow({"node_type": "INPUT", "input_schema": big,
                    "instruction": "", "prefilled": {}})
        assert r.kept_bytes > 5000, "fixture must exceed the withdrawn cap"
        assert "input_schema" in r.kept

    def test_the_budget_never_sacrifices_a_run_deciding_key(self):
        """Over budget, payload-shaped contract keys go first and the terminal
        keys stay — dropping ``skipped`` would fail a run that succeeded."""
        r = narrow({
            "skipped": True,
            "reason": "failure_routed",
            "error_code": "E",
            "source_columns": ["c" * 100 for _ in range(TASK_METADATA_MAX_BYTES // 50)],
        })
        assert r.kept["skipped"] is True
        assert r.kept["reason"] == "failure_routed"
        assert "source_columns" in r.dropped_keys

    def test_it_never_raises_and_never_mutates(self):
        original = {"raw": 1, "error_code": "E"}
        snapshot = dict(original)
        narrow(original)
        assert original == snapshot
        for bad in (None, [], "text", 42, {"k": object()}):
            narrow(bad)  # must not raise

    def test_a_non_dict_narrows_to_empty(self):
        """A worker returning a list instead of an object: decline to store the
        wrong shape rather than persist it."""
        assert narrow(["a", "b"]).kept == {}
