"""SAP HANA implementation of IWorkflowRepository.

Persists Workflow entities to AIW_WORKFLOWS table.
Nodes, edges, and schemas are serialized as JSON in NCLOB columns.
"""
from __future__ import annotations

import json
from typing import Any
import uuid

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge, normalize_schema
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.agent_ref import AgentRef
from app.layer2_application.repositories.workflow_repository_interface import (
    IWorkflowRepository,
    TriggerRoute,
)


from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin


class HanaWorkflowRepository(AsyncRepoMixin, IWorkflowRepository):

    def __init__(self, db: Any) -> None:
        self._db = db
        self._table = "AIW_WORKFLOWS"

    def _to_json(self, obj: Any) -> str | None:
        if obj is None:
            return None
        if isinstance(obj, str):
            return obj
        return json.dumps(obj, default=str)

    def _from_json(self, raw: Any) -> Any:
        if raw is None:
            return None
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    def _execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        return self._db.execute_query(sql, params)

    def _execute_write(self, sql: str, params: tuple = ()) -> None:
        self._db.execute_write(sql, params)

    def _upsert(self, columns: list[str], values: tuple) -> None:
        placeholders = ", ".join(f":p{i}" for i in range(len(values)))
        col_str = ", ".join(columns)
        update_str = ", ".join(f"{c} = :p{i}" for i, c in enumerate(columns) if c != "ID")
        sql = (
            f"UPSERT {self._table} ({col_str}) VALUES ({placeholders}) "
            f"WITH PRIMARY KEY"
        )
        self._execute_write(sql, values)

    def _node_to_dict(self, node: Any) -> dict:
        # Serialize AgentRef instances to dicts
        agents_data = [
            a.to_dict() if isinstance(a, AgentRef) else a
            for a in (node.agents or [])
        ]
        return {
            "id": node.id,
            "name": node.name,
            "node_type": node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
            "worker_type": node.worker_type,
            "input_binding": node.input_binding,
            "output_binding": node.output_binding,
            "input_require": node.input_require,
            "config": node.config,
            "x": node.x,
            "y": node.y,
            "node_class": node.node_class,
            "description": node.description,
            "instruction": node.instruction,
            "icon": node.icon,
            "color": node.color,
            "tags": node.tags,
            "in_ports": node.in_ports,
            "out_ports": node.out_ports,
            "node_definition_id": node.node_definition_id,
            "editable": node.editable,
            "action": node.action,
            "agents": agents_data,
            "parameters": node.parameters,
            "definition_snapshot": node.definition_snapshot,
        }

    def _edge_to_dict(self, edge: Any) -> dict:
        return {
            "id": edge.id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "condition": edge.condition,
            "source_port_id": edge.source_port_id,
            "target_port_id": edge.target_port_id,
            "label": edge.label,
            "description": edge.description,
        }

    def _next_revision(self, id: str) -> int:
        """Next monotonic REVISION for a logical workflow ``id``.

        REVISION is the durable "which row is current" key: every save (a new
        version fork OR an in-place edit) takes max(REVISION)+1 for the group, so
        the most-recently-written row always has the highest REVISION. This
        replaces the old ``ORDER BY UPDATED_AT DESC, VERSION DESC`` resolution,
        which returned a stale row on UPDATED_AT ties / cross-instance clock skew
        (VERSION is a random uuid, so it was a meaningless tiebreak).

        ponytail: max()+1 read-then-write can collide if two saves race on the
        SAME logical id (each gets the same number). Single-editor workflow saves
        don't, and get_by_id still falls back to UPDATED_AT/VERSION on a tie.
        Upgrade path if concurrent same-id saves ever matter: a HANA SEQUENCE
        (global NEXTVAL) or a UNIQUE(ID, REVISION) constraint + retry.
        """
        rows = self._execute_query(
            f'SELECT IFNULL(MAX("REVISION"), 0) + 1 AS next_rev FROM {self._table} WHERE "ID" = :p0',
            (id,),
        )
        if not rows or rows[0].get("next_rev") is None:
            return 1
        return int(rows[0]["next_rev"])

    @staticmethod
    def _pick_current(rows: list[dict]) -> dict | None:
        """Pick the current row from a logical id's version rows.

        Highest REVISION wins; UPDATED_AT then VERSION are descending tiebreaks
        only for rows that predate the REVISION backfill (REVISION IS NULL → -1,
        so a real revision always beats a null one)."""
        if not rows:
            return None
        return max(
            rows,
            key=lambda r: (
                r.get("revision") if r.get("revision") is not None else -1,
                r.get("updated_at") or "",
                r.get("version") or "",
            ),
        )

    @staticmethod
    def _current_window(prefer_published: bool = False) -> str:
        """The ``OVER (...)`` clause that picks each logical ID's *current* row.

        Default (``prefer_published=False``): latest-edited wins — highest
        ``REVISION``, then ``UPDATED_AT`` desc. This matches ``_pick_current`` /
        ``get_by_id`` so the LIST and the DETAIL view agree (SA-1491).

        ``prefer_published=True``: a ``PUBLISHED`` row wins over a later ``DRAFT``
        edit for the same ID (published-first), falling back to latest-edited when
        no published version exists. Opt-in per request — the editor's default view
        is unchanged.
        """
        status_pref = (
            "CASE WHEN \"STATUS\" = 'PUBLISHED' THEN 0 ELSE 1 END ASC, "
            if prefer_published else ""
        )
        return (
            f'PARTITION BY "ID" ORDER BY {status_pref}'
            '"REVISION" DESC NULLS LAST, "UPDATED_AT" DESC'
        )

    def save(self, workflow: Workflow) -> None:
        # Serialize workflow agents to JSON
        agents_data = [
            a.to_dict() if isinstance(a, AgentRef) else a
            for a in (workflow.agents or [])
        ]
        revision = self._next_revision(workflow.id)
        columns = [
            "ID", "WORKFLOW_ID", "NAME", "VERSION", "DESCRIPTION",
            "INPUT_SCHEMA", "OUTPUT_SCHEMA", "NODES", "EDGES",
            "STATUS", "CREATED_AT", "METADATA", "UPDATED_AT",
            "SCHEMA_VERSION", "PURPOSE", "TEST_RUN_ID","ROUTING_PATH",
            "MAIN_FLOW",
            "ACTION", "INSTRUCTION", "AGENTS_JSON", "VARIABLES",
            "CREATED_BY", "UPDATED_BY", "OWNER",
            "REVISION",
        ]
        values = (
            workflow.id,
            workflow.workflow_id,
            workflow.name,
            workflow.version,
            workflow.description,
            self._to_json(workflow.input_schema),
            self._to_json(workflow.output_schema),
            self._to_json([self._node_to_dict(n) for n in workflow.nodes]),
            self._to_json([self._edge_to_dict(e) for e in workflow.edges]),
            workflow.status.value if isinstance(workflow.status, WorkflowStatus) else workflow.status,
            workflow.created_at,
            self._to_json(workflow.metadata),
            workflow.updated_at,
            workflow.schema_version,
            workflow.purpose,
            workflow.test_run_id,
            workflow.routing_path,
            1 if workflow.main_flow else 0,
            workflow.action,
            workflow.instruction,
            self._to_json(agents_data),
            self._to_json(workflow.variables or {}),
            workflow.created_by,
            workflow.updated_by,
            workflow.owner,
            revision,
        )
        self._upsert(columns, values)
        self._sync_relations(workflow)
    
    def asave(self, workflow):
        return self._db.arun(self.save, workflow)

    def _sync_relations(self, workflow: Workflow) -> None:
        """Extract WORKFLOW-type node references and update the relations table."""
        relations = [
            (node.id, node.config.get("workflow_id"))
            for node in workflow.nodes
            if node.node_type == NodeType.WORKFLOW and node.config.get("workflow_id")
        ]
        self.delete_workflow_relations(workflow.id)
        if relations:
            self.save_workflow_relations(workflow.id, relations)

    def get_by_id(self, id: str) -> Workflow | None:
        # The table holds one row per VERSION; "current" = the highest REVISION
        # (the most recently saved row). We fetch the id's version rows and pick
        # in Python via _pick_current rather than ORDER BY ... LIMIT 1 in SQL, so
        # resolution does not depend on HANA string-sort/NULL/collation quirks and
        # is unit-testable. The old ORDER BY UPDATED_AT DESC, VERSION DESC returned
        # a stale row on UPDATED_AT ties / clock skew (VERSION is a random uuid).
        # ponytail: fetches all version rows for the id — fine for the small
        # version counts workflows have; if one ever accrues thousands of versions,
        # switch to ORDER BY REVISION DESC NULLS LAST LIMIT 1.
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0", (id,)
        )
        current = self._pick_current(rows)
        if current is None:
            return None
        return self._row_to_entity(current)

    def get_by_latest_publish_version(self, id: str) -> Workflow | None:
        """
        Return latest PUBLISHED version if one exists, else latest version (incl. DRAFT).

        "Latest" within a status is the highest REVISION (see _pick_current).
        """
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0 AND STATUS IN ('PUBLISHED', 'DRAFT')",
            (id,),
        )
        if not rows:
            return None
        published = [r for r in rows if (r.get("status") or "").upper() == "PUBLISHED"]
        current = self._pick_current(published or rows)
        if current is None:
            return None
        return self._row_to_entity(current)
    
    def get_by_id_version(self, id: str, version: str) -> Workflow | None:
        if not id or version is None:
            return []
        
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0 AND VERSION = :p1", (id, version)
        )
        if not rows:
            return None
        result = self._row_to_entity(rows[0])
        return result

    def get_by_routing_path(self, routing_path: str) -> Workflow | None:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE TO_NVARCHAR(ROUTING_PATH) = :p0 AND MAIN_FLOW = 1", (routing_path,)
        )
        if not rows:
            return None
        return self._row_to_entity(rows[0])

    def list(self) -> list[Workflow]:
        rows = self._execute_query(f"SELECT * FROM {self._table}")
        return [self._row_to_entity(r) for r in rows]

    _FILTERABLE_COLUMNS = {
        "id": ("ID", str),
        "name": ("NAME", str),
        "version": ("VERSION", str),
        "description": ("DESCRIPTION", str),
        "status": ("STATUS", str),
        "routing_path": ("ROUTING_PATH", str),
        "purpose": ("PURPOSE", str),
        "schema_version": ("SCHEMA_VERSION", str),
        "test_run_id": ("TEST_RUN_ID", str),
        "action": ("ACTION", str),
        "instruction": ("INSTRUCTION", str),
        "main_flow": ("MAIN_FLOW", lambda v: 1 if str(v).lower() in ("true", "1") else 0),
        "created_at": ("CREATED_AT", str),
        "updated_at": ("UPDATED_AT", str),
    }
    _CONTAINS_COLUMNS = frozenset({
        "id", "name", "version", "description", "status", "routing_path",
        "purpose", "schema_version", "test_run_id", "action", "instruction",
    })
    _SQL_OPS = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}

    def list_paged(self, filters: Any) -> any:
        from app.layer2_application.repositories.workflow_repository_interface import ListWorkflowsPage
        conditions: list[str] = []
        params: list = []
        idx = 0
        for op_name, sql_op in self._SQL_OPS.items():
            for attr, val in (getattr(filters, op_name, None) or {}).items():
                spec = self._FILTERABLE_COLUMNS.get(attr.lower())
                if spec is None:
                    continue
                col, coerce = spec
                conditions.append(f"{col} {sql_op} :p{idx}")
                params.append(coerce(val))
                idx += 1
        for attr, search in (getattr(filters, "contains", None) or {}).items():
            if attr.lower() not in self._CONTAINS_COLUMNS:
                continue
            col = attr.upper()
            conditions.append(f"LOWER({col}) LIKE :p{idx}")
            params.append(f"%{search.lower()}%")
            idx += 1
        where = " AND ".join(conditions) if conditions else "1=1"
        # count_sql = f"SELECT COUNT(*) AS cnt FROM {self._table} WHERE {where}"
        # count_rows = self._execute_query(count_sql, tuple(params))
        # total = count_rows[0]["cnt"] if count_rows else 0

        total = 0
        order = "CREATED_AT DESC "
        if filters.orderby:
            field = filters.orderby.replace(" desc", "").replace(" asc", "").strip().upper()
            direction = "DESC" if "desc" in filters.orderby else "ASC"
            # ponytail: ORDER BY can't be a bind param (it's an identifier), so an
            # allow-list is the only safe option — $orderby is user-supplied.
            if field in {spec[0] for spec in self._FILTERABLE_COLUMNS.values()}:
                order = f"{field} {direction} "

        if filters.id is not None and filters.id.strip() != "":
            # Bind every value — $id/$status/$version are user-supplied. The old
            # f-string build was SQL-injectable, and double-quoted STATUS="x"/
            # VERSION="x" were HANA *identifiers*, not string literals.
            conds = ["ID = :p0"]
            params = [filters.id]
            if filters.status:
                conds.append(f"STATUS = :p{len(params)}")
                params.append(filters.status)
            if filters.version:
                conds.append(f"VERSION = :p{len(params)}")
                params.append(filters.version)
            where = " AND ".join(conds)

            count_sql = f"SELECT COUNT(*) AS cnt FROM {self._table} WHERE {where}"
            count_rows = self._execute_query(count_sql, tuple(params))
            total = count_rows[0]["cnt"]

            data_sql = (
                f"SELECT * FROM {self._table} "
                f"WHERE {where} "
                f"ORDER BY {order}"
                f"LIMIT {filters.top} OFFSET {filters.skip}"
            )
        else:
            # SA-1491 (F9+F10): the list's "current row per ID" MUST use the same
            # rule as ``_pick_current`` / ``get_by_id`` — REVISION-first, i.e.
            # latest-edited wins — so the LIST and the DETAIL view agree (they
            # previously diverged: the list preferred PUBLISHED, so editing a
            # published workflow left the list showing the pre-edit version while
            # the detail showed the new draft). And count + data MUST share ONE
            # window ordering, else the paginated ``total`` (count's representative
            # row per group) disagrees with the returned rows (data's) under a
            # status filter. PUBLISHED is a status field, not a resolution
            # preference here — ``start_run`` still pins via
            # ``get_by_latest_publish_version``.
            current_window = self._current_window(
                getattr(filters, "prefer_published", False)
            )
            count_sql = (
                f"SELECT COUNT(*) AS cnt FROM (SELECT *, ROW_NUMBER() OVER ({current_window}) AS row_num "
                f"FROM {self._table}) "
                f"WHERE row_num = 1 AND {where}"
            )
            count_rows = self._execute_query(count_sql, tuple(params))
            total = count_rows[0]["cnt"]

            data_sql = (
                f"SELECT * FROM (SELECT *, ROW_NUMBER() OVER ({current_window}) AS row_num "
                f"FROM {self._table}) "
                f"WHERE row_num = 1 AND {where} "
                f"ORDER BY {order}"
                f"LIMIT {filters.top} OFFSET {filters.skip}"
            )

        rows = self._execute_query(data_sql, tuple(params))
        items = [self._row_to_entity(r) for r in rows]
        return ListWorkflowsPage(items=items, total=total)

    def delete(self, id: str) -> None:
        self._execute_write(
            f"DELETE FROM {self._table} WHERE ID = :p0", (id,)
        )
        self.delete_workflow_relations(id)

    def delete_all(self) -> int:
        count_rows = self._execute_query(f"SELECT COUNT(*) AS cnt FROM {self._table}")
        count = count_rows[0]["cnt"] if count_rows else 0
        self._execute_write(f"DELETE FROM {self._table}")
        return count

    def list_main_flows(self) -> list[Workflow]:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE MAIN_FLOW = 1"
        )
        return [self._row_to_entity(r) for r in rows]

    def list_trigger_routes(
        self, trigger_types: tuple[str, ...] = (),
    ) -> list["TriggerRoute"]:
        """Automatic-trigger routes — **PUBLISHED main-flow rows only**.

        Replaces the ``list_main_flows()``-based scan, which was wrong twice:

        1. **Version-blind.** This table holds one row per VERSION and publish
           only DEMOTES the previous row to DRAFT (``publish_workflow_use_case``
           :94-97) — it never deletes it. ``WHERE MAIN_FLOW = 1`` therefore kept
           every version ever saved routable. Measured on tenant-1 2026-08-11:
           150 rows for 20 logical workflows, **8 live event routes of which 7
           were stale rows** — including a topic (``t.jirakkk``) that only ever
           existed in a DRAFT, and two workflows whose PUBLISHED trigger was
           ``manual`` yet still matched ``t.jira`` from 3 and 4 old rows. The
           only thing stopping those from starting runs was the *old row's own*
           ``event_filter``, which the author can no longer see or edit.
        2. **``SELECT *``.** It dragged back ``EDGES`` and the schemas for every
           row: 2568 KB per scan, on the shared event loop. Three columns over
           the published rows is 215 KB — and the routing cache entry that holds
           the result drops from 384 KB to 16 KB.

        Strict ``STATUS = 'PUBLISHED'`` rather than a published-preferred window
        (``_current_window(prefer_published=True)``) is the deliberate choice:
        publish is the act that ARMS an automatic trigger. A never-published
        draft firing on a live event is a side effect on real data that nobody
        asked for. Licensed by two live counts — 0/20 workflows had more than
        one PUBLISHED row (so the routing row IS the row ``StartRunUseCase``
        executes), and 0 of the 11 never-published main flows carried an
        event/schedule trigger (so nothing went dark on the flip).

        Manual StartRun keeps its DRAFT fallback (``get_by_latest_publish_version``)
        on purpose: a human pressing Run is the gate that publish provides here.

        ponytail: still O(published main flows) per scan. The next step, if
        volume demands it, is a derived trigger table (measured: 9.6 KB) — NOT
        taken now, because it adds a write path that must stay in sync, and
        drifted write paths are exactly what cost this service its last four
        silent regressions.
        """
        from app.layer2_application.repositories.workflow_repository_interface import (
            build_trigger_routes,
        )

        rows = self._execute_query(
            f'SELECT "ID", "VERSION", "NODES" FROM {self._table} '
            f"WHERE MAIN_FLOW = 1 AND STATUS = 'PUBLISHED'"
        )
        routes: list["TriggerRoute"] = []
        for row in rows:
            routes.extend(build_trigger_routes(
                row.get("id"),
                row.get("version"),
                self._from_json(row.get("nodes")) or [],
                trigger_types,
            ))
        return routes

    def get_all_child_workflows(self, workflow_id: str) -> list[dict]:
        """Get all child workflows nested within a given workflow.
        
        Calls the stored procedure GET_ALL_CHILD_WORKFLOWS_IN_WORKFLOW to retrieve
        all workflows that are nested/referenced within the specified workflow.
        
        Args:
            workflow_id: The root workflow ID to search for nested workflows
            
        Returns:
            A list of dictionaries containing ID, NAME, and parsed NODES for each child workflow
        """
        rows = []
        try:
            rows = self._db.execute_procedure(
                "GET_ALL_CHILD_WORKFLOWS_IN_WORKFLOW",
                (workflow_id,)
            )
        except Exception as e:
            print(f"Error while executing procedure: {e}")
            return []
        # Parse NODES JSON for each result
        result = []
        for row in rows:
            row_copy = dict(row)
            row_copy["nodes"] = self._from_json(row.get("nodes"))
            result.append(row_copy)
        return result

    def save_workflow_relations(self, workflow_id: str, relations: list[tuple[str, str]]) -> None:
        """Save direct child relations. relations = [(node_id, child_workflow_id), ...]"""
        for node_id, child_workflow_id in relations:
            sql = (
                "UPSERT \"AIW_WORKFLOW_RELATIONS\" "
                "(\"PARENT_WORKFLOW_ID\", \"CHILD_WORKFLOW_ID\", \"NODE_ID\") "
                "VALUES (:p0, :p1, :p2) WITH PRIMARY KEY"
            )
            self._execute_write(sql, (workflow_id, child_workflow_id, node_id))

    def delete_workflow_relations(self, workflow_id: str) -> None:
        self._execute_write(
            "DELETE FROM \"AIW_WORKFLOW_RELATIONS\" WHERE \"PARENT_WORKFLOW_ID\" = :p0",
            (workflow_id,),
        )

    def get_child_workflow_ids(self, workflow_id: str) -> list[dict]:
        rows = self._execute_query(
            "SELECT \"PARENT_WORKFLOW_ID\", \"CHILD_WORKFLOW_ID\", \"NODE_ID\" "
            "FROM \"AIW_WORKFLOW_RELATIONS\" WHERE \"PARENT_WORKFLOW_ID\" = :p0",
            (workflow_id,),
        )
        return [dict(r) for r in rows]

    def get_workflows_by_ids(self, ids: list[str]) -> list[Workflow]:
        if not ids:
            return []
        placeholders = ", ".join(f":p{i}" for i in range(len(ids)))
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE ID IN ({placeholders})",
            tuple(ids),
        )
        return [self._row_to_entity(r) for r in rows]

    def get_latest_version_by_workflow_id(self, workflow_id: str) -> int | None:
        rows = self._execute_query(
            f"SELECT MAX(\"VERSION\") AS max_ver "
            f"FROM {self._table} WHERE \"ID\" = :p0",
            (workflow_id,),
        )
        if not rows or rows[0].get("max_ver") is None:
            return None
        return int(rows[0]["max_ver"])

    def _row_to_entity(self, row: dict) -> Workflow:
        nodes_data = self._from_json(row.get("nodes")) or []
        edges_data = self._from_json(row.get("edges")) or []

        nodes = [
            WorkflowNode(
                id=n.get("id", ""),
                workflow_id=n.get("workflow_id", ""),
                name=n.get("name", ""),
                node_type=n.get("node_type", n.get("type", "TASK")),
                worker_type=n.get("worker_type", n.get("worker_id")) or None,
                parameters=n.get("parameters", {}),
                input_binding=n.get("input_binding", {}),
                output_binding=n.get("output_binding", {}),
                input_require=n.get("input_require", []),
                config=n.get("config", {}),
                x=n.get("x"),
                y=n.get("y"),
                node_class=n.get("node_class", ""),
                description=n.get("description", ""),
                instruction=n.get("instruction", ""),
                icon=n.get("icon", ""),
                color=n.get("color", ""),
                tags=n.get("tags", []),
                in_ports=n.get("in_ports", []),
                out_ports=n.get("out_ports", []),
                node_definition_id=n.get("node_definition_id"),
                editable=n.get("editable", True),
                action=n.get("action", ""),
                agents=n.get("agents", []), # Will be converted to AgentRef in __post_init__
                definition_snapshot=n.get("definition_snapshot"),
            )
            for n in nodes_data
        ]
        edges = [
            WorkflowEdge(
                **({"id": e["id"]} if e.get("id") else {}),
                source_node_id=e.get("source_node_id", ""),
                target_node_id=e.get("target_node_id", ""),
                condition=e.get("condition"),
                source_port_id=e.get("source_port_id", ""),
                target_port_id=e.get("target_port_id", ""),
                label=e.get("label", ""),
                description=e.get("description", ""),
            )
            for e in edges_data
        ]

        return Workflow(
            id=row["id"],
            workflow_id=row["workflow_id"],
            name=row.get("name", ""),
            version=row.get("version"),
            description=row.get("description", ""),
            input_schema=normalize_schema(self._from_json(row.get("input_schema"))),
            output_schema=normalize_schema(self._from_json(row.get("output_schema"))),
            nodes=nodes,
            edges=edges,
            status=WorkflowStatus(row.get("status", "DRAFT")),
            created_at=row.get("created_at", ""),
            purpose=row.get("purpose", ""),
            metadata=self._from_json(row.get("metadata")) or {},
            updated_at=row.get("updated_at", ""),
            schema_version=row.get("schema_version", "1.0.0"),
            test_run_id=row.get("test_run_id"),
            routing_path=row.get("routing_path","/"),
            main_flow=bool(row.get("main_flow", 0)),
            action=row.get("action") or "",
            instruction=row.get("instruction") or "",
            agents=self._from_json(row.get("agents_json")) or [],
            variables=self._from_json(row.get("variables")) or {},
            created_by=row.get("created_by") or None,
            updated_by=row.get("updated_by") or None,
            owner=row.get("owner") or None,
        )

    async def aget_by_id(self, id: str) -> Workflow | None:
        return await self._db.arun(self.get_by_id, id)
    
    async def aget_by_id_version(self, id: str, workflow_version: str) -> Workflow | None:
        return await self._db.arun(self.get_by_id_version, id, workflow_version)
    
    async def aget_by_latest_publish_version(self, id: str) -> Workflow | None:
        return await self._db.arun(self.get_by_latest_publish_version, id)

    def get_list_version_by_status(self, id: str, version: str, status: str) -> list[Workflow]:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} "
            f"WHERE ID = :p0 and VERSION != :p1 and STATUS = :p2 "
            "ORDER BY UPDATED_AT ASC",
            (id, version, status)
        )
        if not rows:
            return []
        return [self._row_to_entity(r) for r in rows]

    async def aget_list_version_by_status(self, id: str, version: str, status: str) -> list[Workflow]:
        return await self._db.arun(self.get_list_version_by_status, id, version, status)