"""The write half of ``SCHEMA_REF_MODE=on``: the EVENT gets a thin ref.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §19.

`columns` + `column_types` are **90.4%** of every descriptor's bytes on the wire
(4,823,808 + 3,924,597 chars against `file_id`'s 169,506, over 3,975 refs). The
`schema_hash` beside them names the same pair in `AIW_SCHEMA_DEF`, so the event
can carry the key instead of the value.

The invariant that makes this safe rather than lossy has its own class below:
**the mirror must still receive FULL refs.** `AIW_RUN_NODE_OUTPUT` is what the
schema is looked up *from* — thinning the row too would leave nothing to expand
from, and the reader's failure mode is not an error but a guess
(`CsvConverter._unflatten` → `_try_parse_value`).
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref, schema_hash
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.data import schema_dictionary as sd
from app.layer2_application.orchestration.orchestration_engine import (
    OrchestrationEngine,
)

COLS = ["sku", "qty"]
TYPES = {"sku": "string", "qty": "number"}
DIGEST = schema_hash(COLS, TYPES)


class _SpyRepo:
    def __init__(self) -> None:
        self.saved: list[dict] = []
        self.schemas_ensured: list[dict] = []

    def save(self, **kw):
        self.saved.append(kw)
        return 1

    def ensure_schemas(self, file_ref):
        self.schemas_ensured.append(file_ref)
        return 1


def _engine(repo=None):
    eng = OrchestrationEngine.__new__(OrchestrationEngine)
    eng._node_output_repo = repo
    eng._event_publisher = None
    eng._data_store_service = None
    return eng


def _ref(file_id="f1"):
    return build_file_ref(file_id=file_id, row_count=2, columns=COLS,
                          column_types=TYPES)


def _workflow():
    return Workflow(id="wf", name="WF",
                    nodes=[WorkflowNode(id="n1", name="First",
                                        node_type=NodeType.TASK)],
                    edges=[])


def _run():
    return WorkflowRun(id="r1", workflow_id="wf", workflow_version="1",
                       context=RunContext(topology=GraphTopology()))


class TestOffAndShadowLeaveTheWireAlone:
    @pytest.mark.parametrize("mode", ["off", "shadow"])
    def test_the_ref_keeps_its_schema(self, monkeypatch, mode):
        """Shadow observes. A mode that also changed the wire would make the two
        indistinguishable — and shadow is the evidence `on` rests on."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", mode)
        wrapped = {"n1": _ref()}
        out = _engine(_SpyRepo())._thin_for_the_wire(wrapped)
        assert out["n1"]["columns"] == COLS

    def test_off_writes_no_schema_entries(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "off")
        repo = _SpyRepo()
        _engine(repo)._thin_for_the_wire({"n1": _ref()})
        assert repo.schemas_ensured == []


class TestOnThinsTheEvent:
    def test_the_schema_is_dropped_and_the_hash_kept(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        out = _engine(_SpyRepo())._thin_for_the_wire({"n1": _ref()})
        assert "columns" not in out["n1"] and "column_types" not in out["n1"]
        assert out["n1"]["schema_hash"] == DIGEST
        assert out["n1"]["file_id"] == "f1"

    def test_the_dictionary_entry_is_written_FIRST(self, monkeypatch):
        """The ordering that makes it safe: the row mirror runs AFTER the
        publish (it must never lead the event stream), so without this a thin
        ref could reach a reader before its schema existed — and that reader
        guesses types rather than failing."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        repo = _SpyRepo()
        _engine(repo)._thin_for_the_wire({"n1": _ref()})
        assert len(repo.schemas_ensured) == 1
        assert repo.schemas_ensured[0]["columns"] == COLS

    def test_inline_values_pass_through_untouched(self, monkeypatch):
        """`code` / `approval_flow` keep raw values — no ref, no hash, nothing
        to thin."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        out = _engine(_SpyRepo())._thin_for_the_wire({"n1": {"result": [1, 2]}})
        assert out["n1"] == {"result": [1, 2]}

    def test_several_nodes_are_each_thinned(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        out = _engine(_SpyRepo())._thin_for_the_wire(
            {"n1": _ref("f1"), "n2": _ref("f2")})
        assert all("columns" not in v for v in out.values())


class TestTheMirrorStillGetsFullRefs:
    """The invariant. The row is what the schema is looked up FROM."""

    def test_wrapped_is_not_mutated(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        wrapped = {"n1": _ref()}
        _engine(_SpyRepo())._thin_for_the_wire(wrapped)
        assert wrapped["n1"]["columns"] == COLS, (
            "thinning must not reach the value the mirror is about to store"
        )

    def test_the_row_written_carries_the_schema(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        repo = _SpyRepo()
        eng = _engine(repo)
        wrapped = {"n1": _ref()}
        eng._thin_for_the_wire(wrapped)
        eng._record_node_output(wrapped, _run(), _workflow())
        assert repo.saved[0]["file_ref"]["columns"] == COLS


class TestItCannotBreakAPublish:
    def test_no_repo_still_thins(self, monkeypatch):
        """A deployment without the table can still shrink its events; there is
        simply nothing to look them up from, so this would only be set where the
        repo exists. It must not crash either way."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        out = _engine(None)._thin_for_the_wire({"n1": _ref()})
        assert "columns" not in out["n1"]

    def test_a_raising_repo_falls_back_to_full_refs(self, monkeypatch):
        """Fail SAFE, not small: if the schema cannot be stored, publish the
        schema. A fat event beats an unreadable one."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")

        class _Boom:
            def ensure_schemas(self, _ref):
                raise RuntimeError("hana down")

        out = _engine(_Boom())._thin_for_the_wire({"n1": _ref()})
        assert out["n1"]["columns"] == COLS

    def test_an_empty_write_set(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        assert _engine(_SpyRepo())._thin_for_the_wire({}) == {}


@pytest.mark.asyncio
class TestItIsACTUALLYWIRED:
    async def test_the_published_event_carries_thin_refs(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        published = []
        eng = _engine(_SpyRepo())
        eng._publish_now = lambda topic, event: published.append(event)

        await eng.apply_state_updates_as_files(
            {"n1": _ref()}, _run(), _workflow(), source_node_id="n1")

        assert published, "the event must still be published"
        ref = published[0].state_updates["n1"]
        assert "columns" not in ref, (
            "apply_state_updates_as_files must thin the event — this is the "
            "only place the 90.4% saving is actually taken"
        )
        assert ref["schema_hash"] == DIGEST

    async def test_off_publishes_full_refs(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "off")
        published = []
        eng = _engine(_SpyRepo())
        eng._publish_now = lambda topic, event: published.append(event)
        await eng.apply_state_updates_as_files(
            {"n1": _ref()}, _run(), _workflow(), source_node_id="n1")
        assert published[0].state_updates["n1"]["columns"] == COLS
