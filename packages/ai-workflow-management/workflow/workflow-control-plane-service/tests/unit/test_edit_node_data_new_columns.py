"""Unit: PATCH /data auto-adds columns for data not in the initial schema.

Covers both artifact paths of ``EditNodeDataUseCase``:

- **Object root** (scalar form): a new key upserts locally and is appended as a
  new column (via ``csv_ops.upsert_scalar_row``) — never forwarded to the
  factory. Previously this silently dropped the key (200 OK, data lost) or, on
  a second edit, raised ``INTERNAL_ERROR`` and FAILED the run.
- **Array table**: a row insert/edit whose ``data`` carries an unknown column
  first creates that column via the data-factory column transform
  (``INodeDataFactoryService.add_columns``) and only then forwards the row op —
  because the row endpoint rejects unknown columns. Known-column inserts skip
  the pre-add. The new column lands in the working ref's ``columns`` metadata
  (what GET /data displays).
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.interfaces.node_data_factory_service_interface import (
    RowTransformResult,
)
from app.layer1_domain.value_objects.node_input_etag import (
    NodeInputEtag,
)
from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataCommand,
    EditNodeDataUseCase,
)

pytestmark = pytest.mark.asyncio

RUN_ID = "run-1"
NODE_ID = "input-node"
TASK_ID = "run-1-input-node"


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Run:
    id = RUN_ID
    workflow_id = "wf-1"


class _Node:
    id = NODE_ID
    node_type = NodeType.INPUT


class _Workflow:
    def get_node(self, node_id):
        return _Node() if node_id == NODE_ID else None


class _Task:
    def __init__(self):
        self.id = TASK_ID
        self.node_id = NODE_ID
        self.status = TaskStatus.PENDING
        self.run_generation = 0
        self.resolved_input = build_file_ref(file_id="")
        self.output_metadata = {}


class _RunRepo:
    async def aget_by_id(self, run_id):
        return _Run()


class _TaskRepo:
    def __init__(self, task):
        self._task = task

    async def alist_by_run(self, run_id):
        return [self._task]


class _View:
    def __init__(self, variables):
        self.variables = variables


class _ViewStore:
    def __init__(self, variables):
        self._view = _View(variables)

    def get(self, run_id):
        return self._view


class _Engine:
    def __init__(self):
        self.edited = []

    def load_workflow_for_run(self, run):
        return _Workflow()

    def emit_node_data_edited(self, run, workflow, *, new_ref, **k):
        self.edited.append(new_ref)


class _MemFS:
    """Stateful in-memory file service (mints a fresh file_id per upload)."""

    def __init__(self, seed=None):
        self.store = dict(seed or {})
        self.deleted = []
        self._n = 0

    async def download_file(self, file_id):
        return self.store.get(file_id, b"")

    async def upload_file(self, content, filename, content_type):
        self._n += 1
        fid = f"up-{self._n}"
        self.store[fid] = content
        return {"file_id": fid}

    async def delete_file(self, file_id):
        self.deleted.append(file_id)
        self.store.pop(file_id, None)


class _FakeFactory:
    """Records calls; threads a fresh version per op (file_id stays stable)."""

    def __init__(self):
        self.add_calls = []
        self.row_calls = []
        self._v = 0

    async def add_columns(self, file_id, columns, *, default="", version_id=None):
        self.add_calls.append(
            {"file_id": file_id, "columns": list(columns), "version_id": version_id}
        )
        self._v += 1
        return RowTransformResult(success=True, file_id=file_id, version_id=f"v{self._v}")

    async def apply_row_operations(self, file_id, operations, file_format="csv", version_id=None):
        self.row_calls.append(
            {"file_id": file_id, "version_id": version_id, "operations": operations}
        )
        self._v += 1
        return RowTransformResult(
            success=True, file_id=file_id, version_id=f"v{self._v}", affected_rows=1,
        )


def _use_case(working_ref, fs, factory, engine):
    return EditNodeDataUseCase(
        logger=None,
        run_repo=_RunRepo(),
        run_context_view_store=_ViewStore({NODE_ID: working_ref}),
        task_repo=_TaskRepo(_Task()),
        workflow_repo=None,
        file_service_client=fs,
        node_input_etag_calculator=NodeInputEtag(),
        node_data_factory_service=factory,
        orchestration_engine=engine,
    )


def _etag(working_ref):
    return NodeInputEtag().of(working_ref, schema_version="v0")


def _cmd(artifact_id, operations, working_ref):
    return EditNodeDataCommand(
        run_id=RUN_ID, node_id=NODE_ID, task_id=TASK_ID,
        if_match=_etag(working_ref),
        artifacts=[{"artifact_id": artifact_id, "operations": operations}],
    )


# ---------------------------------------------------------------------------
# Object root
# ---------------------------------------------------------------------------


async def test_object_root_insert_new_key_adds_column_not_via_factory():
    root = build_file_ref(file_id="root-0")
    root["artifact_type"] = "object_field"
    root["columns"] = ["__row_id", "input"]
    fs = _MemFS({"root-0": b"__row_id,input\r\n"})
    factory = _FakeFactory()
    engine = _Engine()
    uc = _use_case(root, fs, factory, engine)

    res = await uc.execute(_cmd(
        "__root__",
        [{"operation": "insert", "row_id": None,
          "data": {"input": "x", "newcol": "y"}}],
        root,
    ))

    assert res.error is None, res.error
    assert res.results[0].status == "ok"
    assert factory.add_calls == []          # object path stays local
    assert factory.row_calls == []
    new_ref = engine.edited[-1]
    assert "newcol" in new_ref["columns"]   # display metadata expanded
    assert b"newcol" in fs.store[new_ref["file_id"]]  # column in the file too
    assert b"y" in fs.store[new_ref["file_id"]]


# ---------------------------------------------------------------------------
# Array table
# ---------------------------------------------------------------------------


def _root_with_table(table_columns):
    root = build_file_ref(file_id="root-0")
    root["artifact_type"] = "object_field"
    root["columns"] = ["__row_id"]
    root["artifacts"] = [{
        "artifact_id": "tbl",
        "artifact_type": "array_field",
        "file_id": "tbl-0",
        "version_id": "",
        "columns": list(table_columns),
    }]
    return root


def _table(new_ref):
    return next(a for a in new_ref["artifacts"] if a["artifact_id"] == "tbl")


async def test_table_insert_new_column_pre_adds_via_factory():
    root = _root_with_table(["__row_id", "a", "b"])
    fs = _MemFS({"root-0": b"__row_id\r\n", "tbl-0": b"__row_id,a,b\r\n"})
    factory = _FakeFactory()
    engine = _Engine()
    uc = _use_case(root, fs, factory, engine)

    res = await uc.execute(_cmd(
        "tbl",
        [{"operation": "insert", "row_id": None,
          "data": {"a": "1", "b": "2", "c_new": "3"}}],
        root,
    ))

    assert res.error is None, res.error
    # The unknown column was created BEFORE the row op...
    assert len(factory.add_calls) == 1
    assert factory.add_calls[0]["columns"] == ["c_new"]
    assert factory.add_calls[0]["file_id"] == "tbl-0"
    # ...and the row op pinned to the version the add produced.
    assert len(factory.row_calls) == 1
    assert factory.row_calls[0]["version_id"] == "v1"
    # New column surfaces in the working ref's display metadata.
    assert "c_new" in _table(engine.edited[-1])["columns"]


async def test_table_insert_known_columns_skips_pre_add():
    root = _root_with_table(["__row_id", "a", "b"])
    fs = _MemFS({"root-0": b"__row_id\r\n", "tbl-0": b"__row_id,a,b\r\n"})
    factory = _FakeFactory()
    engine = _Engine()
    uc = _use_case(root, fs, factory, engine)

    res = await uc.execute(_cmd(
        "tbl",
        [{"operation": "insert", "row_id": None, "data": {"a": "1", "b": "2"}}],
        root,
    ))

    assert res.error is None, res.error
    assert factory.add_calls == []     # nothing unknown → no column transform
    assert len(factory.row_calls) == 1


async def test_table_edit_new_column_pre_adds_via_factory():
    root = _root_with_table(["__row_id", "a"])
    fs = _MemFS({"root-0": b"__row_id\r\n", "tbl-0": b"__row_id,a\r\n1,old\r\n"})
    factory = _FakeFactory()
    engine = _Engine()
    uc = _use_case(root, fs, factory, engine)

    res = await uc.execute(_cmd(
        "tbl",
        [{"operation": "edit", "row_id": "1", "data": {"a": "new", "added": "z"}}],
        root,
    ))

    assert res.error is None, res.error
    assert factory.add_calls and factory.add_calls[0]["columns"] == ["added"]
    assert "added" in _table(engine.edited[-1])["columns"]


# ---------------------------------------------------------------------------
# Insert anchor: at_index when (effectively) empty, after when non-empty.
# (The factory rejects after/before index 0 on a 0-row frame.)
# ---------------------------------------------------------------------------


def _insert_positions(factory):
    ops = factory.row_calls[0]["operations"]
    return [o.position for o in ops if o.operation == "insert"]


async def _run_row_op(table_bytes, op):
    root = _root_with_table(["__row_id", "a"])
    fs = _MemFS({"root-0": b"__row_id\r\n", "tbl-0": table_bytes})
    factory = _FakeFactory()
    uc = _use_case(root, fs, factory, _Engine())
    res = await uc.execute(_cmd("tbl", [op], root))
    assert res.error is None, res.error
    return factory


async def test_insert_into_empty_table_uses_at_index():
    factory = await _run_row_op(
        b"__row_id,a\r\n",  # 0 rows
        {"operation": "insert", "row_id": None, "data": {"a": "1"}},
    )
    assert _insert_positions(factory) == ["at_index"]


async def test_insert_into_nonempty_table_uses_after():
    factory = await _run_row_op(
        b"__row_id,a\r\n1,seed\r\n",  # 1 row
        {"operation": "insert", "row_id": None, "data": {"a": "1"}},
    )
    assert _insert_positions(factory) == ["after"]


async def test_edit_single_row_table_uses_at_index():
    # edit = delete + insert; deleting the only row empties the table, so the
    # insert must use at_index.
    factory = await _run_row_op(
        b"__row_id,a\r\n1,old\r\n",  # 1 row
        {"operation": "edit", "row_id": "1", "data": {"a": "new"}},
    )
    assert _insert_positions(factory) == ["at_index"]


async def test_edit_multirow_table_uses_after():
    factory = await _run_row_op(
        b"__row_id,a\r\n1,old\r\n2,other\r\n",  # 2 rows
        {"operation": "edit", "row_id": "1", "data": {"a": "new"}},
    )
    assert _insert_positions(factory) == ["after"]
