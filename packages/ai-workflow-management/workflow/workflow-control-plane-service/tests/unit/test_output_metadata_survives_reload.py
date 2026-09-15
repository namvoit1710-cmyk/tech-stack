"""``AIW_TASKS.OUTPUT_METADATA`` must survive a repo round-trip.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §17.

This guard exists because its absence is the one thing that made the
``DROP CONTEXT`` outage possible to ship. That drop was caught the *first* time
by ``test_hana_run_repo_cas::test_encrypted_credentials_survive_reload_as_dict``,
which went red the moment the write path stopped naming the column. No such test
covered ``OUTPUT_METADATA``: the only three tests touching ``HanaTaskRepository``
are ``test_hana_task_repo_scope_stack``, ``test_active_task_point_lookup`` and
``test_async_repo_mixin``, and none asserts this field survives a reload.

§8 step 5a still reads ``DROP OUTPUT_METADATA``, and §5's DDL proposes
``OUTPUT_METADATA_V2 NVARCHAR(5000)``. §17 measured both to be unsafe. Until
that text is acted on, this file is what turns "unsafe" into a red test.

The danger is specifically that **nothing raises**. Reads are ``SELECT *``, so a
dropped column yields ``row.get("output_metadata") -> None`` and every consumer
below takes its empty-branch in silence — an unresolved ``{{…}}`` template
rendered to a user, a read-only table that accepts edits, a skipped branch leaf
that fails the whole run.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer4_frameworks.persistence.hana.hana_task_repo import HanaTaskRepository


def _repo() -> HanaTaskRepository:
    repo = HanaTaskRepository(MagicMock())
    repo._execute_write = MagicMock()
    return repo


def _saved(repo: HanaTaskRepository) -> tuple[str, tuple]:
    return repo._execute_write.call_args.args


def _reload(repo: HanaTaskRepository, task: Task) -> Task:
    """Save, then rebuild the entity from the row HANA would hand back.

    ``_row_to_entity`` reads lower-cased keys off a ``SELECT *`` row, so the
    round-trip is exercised through the same two functions production uses.
    """
    repo.save(task)
    sql, values = _saved(repo)
    columns = sql.split("(", 1)[1].split(")", 1)[0].split(", ")
    row = {c.strip().lower(): v for c, v in zip(columns, values)}
    return repo._row_to_entity(row)


class TestTheColumnIsWritten:
    def test_save_names_output_metadata(self):
        repo = _repo()
        repo.save(Task(run_id="r", node_id="n", output_metadata={"a": 1}))
        sql, _ = _saved(repo)
        assert "OUTPUT_METADATA" in sql, (
            "save() must name OUTPUT_METADATA — dropping it from base_columns is "
            "the silent half of the failure; dropping the COLUMN while it is "
            "named here is the loud half (every task UPSERT fails)"
        )

    def test_it_is_serialised_as_json(self):
        repo = _repo()
        repo.save(Task(run_id="r", node_id="n", output_metadata={"error_code": "E1"}))
        _sql, values = _saved(repo)
        assert any(
            isinstance(v, str) and json.loads(v) == {"error_code": "E1"}
            for v in values if isinstance(v, str) and v.startswith("{")
        )


class TestEveryControlKeySurvives:
    """One test per key, each naming the reader that breaks without it.

    Batched into a single dict they would all pass on a partial round-trip;
    separately, each failure points at the consumer it costs.
    """

    def test_input_schema_survives(self):
        """schema_providers.py:118 — precedence 1. Lost, GET /schema falls
        through to the design-time branch and the FE receives UNRESOLVED
        ``{{…}}`` templates where the user should see resolved values."""
        schema = [{"name": "email", "type": "string", "label": "Email"}]
        back = _reload(_repo(), Task(run_id="r", node_id="n",
                                     output_metadata={"input_schema": schema}))
        assert back.output_metadata["input_schema"] == schema

    def test_render_config_survives(self):
        """schema_providers.py:235 ``_extract_render_config`` — its docstring
        says missing metadata "yields defaults", so losing these does not raise:
        the DATA_EDIT table silently drops read-only, its action buttons, and
        its select mode."""
        meta = {
            "readonly": True,
            "custom_data_update": True,
            "actions": [{"id": "approve", "label": "Approve"}],
            "settings": [{"key": "dense"}],
            "query_params": {"$top": 50},
            "selectionMode": "multiple",
        }
        back = _reload(_repo(), Task(run_id="r", node_id="n", output_metadata=meta))
        assert back.output_metadata == meta

    def test_skipped_survives(self):
        """workflow_run.py:580 — a FAILED task carrying ``skipped`` is a
        deliberately-routed branch leaf, not a failure. Lost, the run fails."""
        back = _reload(_repo(), Task(run_id="r", node_id="n",
                                     status=TaskStatus.FAILED,
                                     output_metadata={"skipped": True,
                                                      "reason": "failure_routed"}))
        assert back.output_metadata["skipped"] is True

    def test_parent_timeout_reason_survives(self):
        """resume_pending_task_use_case.py:282,299,337 — the resume path
        recognises a timed-out child task by this exact string."""
        back = _reload(_repo(), Task(run_id="r", node_id="n",
                                     output_metadata={"reason": "parent_timeout"}))
        assert back.output_metadata["reason"] == "parent_timeout"

    def test_error_code_survives(self):
        """get_node_output_use_case.py:490,515 — the one key §6 named."""
        back = _reload(_repo(), Task(run_id="r", node_id="n",
                                     output_metadata={"error_code": "WORKER_TIMEOUT"}))
        assert back.output_metadata["error_code"] == "WORKER_TIMEOUT"

    def test_source_node_id_survives(self):
        """data_edit_handler.py:392 — edit lineage."""
        back = _reload(_repo(), Task(run_id="r", node_id="n",
                                     output_metadata={"source_node_id": "upstream"}))
        assert back.output_metadata["source_node_id"] == "upstream"


class TestSizeIsNotCappedAt5000:
    """§17.4, measured on tenant-1: of 204 schema-bearing PENDING nodes in the
    60 heaviest workflows, **113 exceed 5000 chars** and the largest
    reconstructed metadata is 23,468 — 4.7x the cap §5 proposes. And that is the
    DESIGN-TIME schema; the stamped one is resolved, so it is at least as large.

    ``NVARCHAR(5000)`` would not stop payload bloat. It would stop an INPUT node
    from dispatching.
    """

    def test_a_realistic_oversize_schema_round_trips(self):
        schema = [
            {"name": f"field_{i}", "type": "string",
             "label": f"Question number {i}",
             "placeholder": "please answer in full sentences",
             "validation": {"required": i % 3 == 0, "maxLength": 400}}
            for i in range(120)
        ]
        meta = {"node_type": "INPUT", "input_schema": schema,
                "instruction": "Fill this in", "prefilled": {}, "timeout_minutes": 60}
        assert len(json.dumps(meta)) > 5000, "fixture must exceed the proposed cap"

        back = _reload(_repo(), Task(run_id="r", node_id="n", output_metadata=meta))
        assert back.output_metadata == meta
        assert len(back.output_metadata["input_schema"]) == 120


class TestTheEmptyBranchIsSilent:
    """Documents the failure mode rather than preventing it: if the column goes,
    NOTHING on the read path raises. That is why the guard has to live on the
    write side."""

    def test_a_row_without_the_column_reloads_as_none(self):
        repo = _repo()
        task = repo._row_to_entity({
            "id": "t1", "run_id": "r1", "node_id": "n1", "status": "COMPLETED",
        })
        assert task.output_metadata is None
        assert (task.output_metadata or {}).get("input_schema") is None
        assert (task.output_metadata or {}).get("skipped") is None

    @pytest.mark.parametrize("raw", [None, "", "not json", "[1,2,3]"])
    def test_malformed_values_never_raise(self, raw):
        """``_from_json`` swallows bad JSON. A truncating write (what a hard
        NVARCHAR cap plus a permissive driver would produce) therefore lands as
        ``None``, not as an error — silent, exactly like the CONTEXT drop."""
        repo = _repo()
        repo._row_to_entity({
            "id": "t", "run_id": "r", "node_id": "n", "status": "COMPLETED",
            "output_metadata": raw,
        })


class TestNoneIsPreservedNotCoerced:
    def test_absent_metadata_stays_absent(self):
        back = _reload(_repo(), Task(run_id="r", node_id="n"))
        assert back.output_metadata is None

    def test_empty_dict_is_not_confused_with_absent(self):
        """``{}`` means "the worker returned no metadata"; ``None`` means "never
        set". ``_to_json`` maps only None to NULL, so the distinction holds."""
        repo = _repo()
        repo.save(Task(run_id="r", node_id="n", output_metadata={}))
        _sql, values = _saved(repo)
        assert "{}" in [v for v in values if isinstance(v, str)]
