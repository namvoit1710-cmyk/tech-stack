"""SAP HANA implementation of IRunRepository.

Persists WorkflowRun entities to AIW_WORKFLOW_RUNS table.
input_data, output_data, and context are serialized as JSON in NCLOB columns.
"""
import json
import logging
from typing import Any

from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.workflow import WorkflowEdge
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential


def _json_default(obj: Any) -> Any:
    """``json.dumps`` fallback for non-JSON-native values in the run context.

    The encrypted runtime-credentials blob (``context.variables['__credentials']``)
    is an :class:`EncryptedCredential` dataclass. A bare ``default=str`` stringifies
    it to ``"EncryptedCredential(ciphertext_b64='…', key_version=1)"``, so on reload
    it comes back as a STRING that the TASK handler's credential-injection matches
    NONE of (it expects an ``EncryptedCredential`` or a ``{ciphertext_b64,…}`` dict)
    → the credential is silently dropped and every credential-using worker goes out
    unauthenticated → 401. Serialize it to the ``{ciphertext_b64, key_version}`` dict
    the handler's deserialized-path branch reconstructs and decrypts. Everything else
    keeps the previous ``str`` fallback.
    """
    if isinstance(obj, EncryptedCredential):
        return {"ciphertext_b64": obj.ciphertext_b64, "key_version": obj.key_version}
    return str(obj)
from app.layer2_application.repositories.run_repository_interface import (
    IRunRepository,
    ListRunsFilter,
    ListRunsPage,
    OptimisticConcurrencyError,
)


from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin

logger = logging.getLogger(__name__)

#: ``loop_state`` keys the table stores as a ``file_id`` in its own column.
_LOOP_STATE_REF_KEYS = ("items", "iteration_results")


def _loop_state_differences(from_context: dict, from_table: dict) -> list[str]:
    """WHICH keys differ between the two sources — ``[]`` when they agree.

    Returns the differing keys rather than a bool, because the first live run
    through shadow mode produced 22 divergence lines that all read
    ``context=['loop'] table=['loop']`` — the same key set, every time. A signal
    that fires without saying what fired is not a signal: a genuine mapper bug
    and a benign read-your-writes race look identical in the log, and the
    operator has no way to tell them apart. (That run's FINAL state was verified
    identical on all 15 keys, so those 22 were the race.)

    A payload key held as a ``file_ref`` lives in ``ITEMS_REF`` / ``RESULTS_REF``,
    and ``from_row`` deliberately leaves it ABSENT rather than inventing a value
    it has no file-service client to resolve. Comparing it would report a
    divergence on every loop that is working perfectly.
    """
    from app.layer1_domain.entities.data_reference import is_file_ref

    diffs: list[str] = []
    for node_id in sorted(set(from_context) | set(from_table)):
        if node_id not in from_context:
            diffs.append(f"{node_id}:only-in-table")
            continue
        if node_id not in from_table:
            diffs.append(f"{node_id}:only-in-context")
            continue
        ctx_state, tbl_state = from_context[node_id], from_table[node_id]
        if not isinstance(ctx_state, dict) or not isinstance(tbl_state, dict):
            if ctx_state != tbl_state:
                diffs.append(f"{node_id}:not-a-dict")
            continue
        skip = {k for k in _LOOP_STATE_REF_KEYS if is_file_ref(ctx_state.get(k))}
        for key in sorted((set(ctx_state) | set(tbl_state)) - skip):
            if ctx_state.get(key) != tbl_state.get(key):
                diffs.append(f"{node_id}.{key}")
    return diffs


class HanaRunRepository(AsyncRepoMixin, IRunRepository):

    def __init__(
        self,
        db: Any,
        loop_state_repo: Any = None,
        loop_state_read_mode: str = "context",
        node_state_repo: Any = None,
        node_state_read_mode: str = "context",
    ) -> None:
        self._db = db
        self._table = "AIW_WORKFLOW_RUNS"
        # Optional shadow mirror of context.loop_state into AIW_RUN_LOOP_STATE.
        # Optional so every existing construction (tests, sqlite, memory paths)
        # keeps working unchanged; None simply disables the mirror.
        self._loop_state_repo = loop_state_repo
        # "context" | "shadow" | "table" — see _resolve_loop_state.
        self._loop_state_read_mode = loop_state_read_mode
        # The same pair for node_visits / pending_revisits — the OTHER two
        # fields DROP CONTEXT destroys (§13.1). Separate switches on purpose:
        # node_visits sits on the task-id path, so its risk profile differs from
        # loop_state's and the two have to be flippable independently.
        self._node_state_repo = node_state_repo
        self._node_state_read_mode = node_state_read_mode

    def _to_json(self, obj: Any) -> str | None:
        if obj is None:
            return None
        if isinstance(obj, str):
            return obj
        return json.dumps(obj, default=_json_default)

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

    # ── SA-2597: dashboard analytics aggregation ──────────────────────
    # All non-test runs within [cutoff, now]. Tenant filter is optional
    # (single-tenant deployments pass none → all runs). Params (index-based :pN):
    #   :p0 tenant  :p1 cutoff  :p2 owner-id  :p3 name  :p4 status
    #
    # SA-2626: every execution counts — direct (started by user/API/schedule,
    # PARENT_RUN_ID empty) AND as-sub-flow (invoked by a parent flow,
    # PARENT_RUN_ID set). A sub-flow run's WORKFLOW_ID is the *child's* version,
    # so it is attributed to the child flow (not the caller) via the version
    # join — no double-count (parent run and child run are distinct rows). This
    # drives the usage table, the "top workflows" ranking and the trend alike,
    # so a heavily-reused sub-flow surfaces at its real total instead of hidden.
    #
    # SA-2615: AIW_WORKFLOWS holds one row PER VERSION (shared logical "ID",
    # unique "WORKFLOW_ID"). ``_CUR_WORKFLOW`` picks each logical ID's *current*
    # row — PUBLISHED wins, else latest-edited (REVISION then UPDATED_AT) — so the
    # displayed name/owner/status come from the published (or latest) version, not
    # whichever version was created first. Runs (which pin to a version's
    # WORKFLOW_ID) are aggregated per logical ID by joining through every version.
    _CUR_WORKFLOW = (
        'SELECT "ID", "NAME", "OWNER", "CREATED_BY", "STATUS" FROM ('
        'SELECT "ID", "NAME", "OWNER", "CREATED_BY", "STATUS", ROW_NUMBER() OVER ('
        'PARTITION BY "ID" ORDER BY CASE WHEN "STATUS" = \'PUBLISHED\' THEN 0 ELSE 1 END ASC, '
        '"REVISION" DESC NULLS LAST, "UPDATED_AT" DESC) AS rn '
        'FROM "AIW_WORKFLOWS") WHERE rn = 1'
    )

    @staticmethod
    def _analytics_run_filter(with_tenant: bool) -> str:
        # SA-2626: no PARENT_RUN_ID gate — count direct + sub-flow executions.
        f = "r.IS_TEST = FALSE AND r.STARTED_AT >= :p1"
        return f + (" AND r.TENANT_ID = :p0" if with_tenant else "")

    def dashboard_overview(self, cutoff: str, tenant: str | None) -> dict:
        p = (tenant or "", cutoff)
        f = self._analytics_run_filter(bool(tenant))
        total_wf = self._execute_query('SELECT COUNT(DISTINCT "ID") AS c FROM "AIW_WORKFLOWS"')[0]["c"]
        agg = self._execute_query(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN r.STATUS = 'COMPLETED' THEN 1 ELSE 0 END) AS ok, "
            "SUM(CASE WHEN r.STATUS = 'FAILED' THEN 1 ELSE 0 END) AS failed "
            f'FROM "AIW_WORKFLOW_RUNS" r WHERE {f}', p)[0]
        series = self._execute_query(
            "SELECT SUBSTRING(r.STARTED_AT, 1, 10) AS d, "
            "SUM(CASE WHEN r.STATUS = 'FAILED' THEN 0 ELSE 1 END) AS ok, "
            "SUM(CASE WHEN r.STATUS = 'FAILED' THEN 1 ELSE 0 END) AS failed "
            f'FROM "AIW_WORKFLOW_RUNS" r WHERE {f} '
            "GROUP BY SUBSTRING(r.STARTED_AT, 1, 10) ORDER BY d", p)
        top = self._execute_query(
            f"WITH cur AS ({self._CUR_WORKFLOW}) "
            'SELECT cur."NAME" AS name, COUNT(r.ID) AS c '
            'FROM cur JOIN "AIW_WORKFLOWS" allv ON allv."ID" = cur."ID" '
            f'JOIN "AIW_WORKFLOW_RUNS" r ON r.WORKFLOW_ID = allv."WORKFLOW_ID" AND {f} '
            'GROUP BY cur."ID", cur."NAME" ORDER BY c DESC LIMIT 6', p)
        return {"total_workflows": int(total_wf or 0), "agg": agg, "series": series, "top": top}

    def dashboard_workflows(self, cutoff: str, tenant: str | None, owner: str | None,
                            q: str | None, status: str | None,
                            skip: int = 0, top: int = 10) -> dict:
        # SA-2614: server-side pagination for the usage table. The per-workflow
        # aggregate (agg) is computed once; the paged rows LIMIT/OFFSET it, while
        # matched-count / total-runs / top-6 ranking are computed across the WHOLE
        # matched set (not just the page) so charts + KPIs stay global.
        p = (tenant or "", cutoff, owner or "", (q or "").lower(), (status or "").lower())
        conds = ["1 = 1"]
        if owner:
            conds.append('COALESCE(cur."OWNER", cur."CREATED_BY") = :p2')
        if q:
            conds.append('LOWER(cur."NAME") LIKE \'%\' || :p3 || \'%\'')
        if status and status != "all":
            conds.append('LOWER(cur."STATUS") = :p4')
        wsql = " AND ".join(conds)
        # SA-2626: no PARENT_RUN_ID gate — table rows + ranking count all runs.
        run_join = ('r.WORKFLOW_ID = allv."WORKFLOW_ID" '
                    "AND r.IS_TEST = FALSE AND r.STARTED_AT >= :p1"
                    + (" AND r.TENANT_ID = :p0" if tenant else ""))
        dur = ("SECONDS_BETWEEN("
               "TO_TIMESTAMP(REPLACE(SUBSTRING(r.STARTED_AT,1,19),'T',' '),'YYYY-MM-DD HH24:MI:SS'),"
               "TO_TIMESTAMP(REPLACE(SUBSTRING(r.COMPLETED_AT,1,19),'T',' '),'YYYY-MM-DD HH24:MI:SS'))")
        agg_body = (
            'SELECT cur."ID" AS id, cur."NAME" AS name, '
            'COALESCE(cur."OWNER", cur."CREATED_BY") AS owner, LOWER(cur."STATUS") AS status, '
            "COUNT(r.ID) AS runs, "
            # SA-2626: direct vs as-sub-flow split — feeds the Runs-cell hover
            # breakdown on the FE ("was N direct + M as sub-flow"). The column
            # itself stays a single total (runs = direct + sub).
            "SUM(CASE WHEN r.PARENT_RUN_ID = '' OR r.PARENT_RUN_ID IS NULL THEN 1 ELSE 0 END) AS direct, "
            "SUM(CASE WHEN r.PARENT_RUN_ID IS NOT NULL AND r.PARENT_RUN_ID <> '' THEN 1 ELSE 0 END) AS sub, "
            "SUM(CASE WHEN r.STATUS = 'COMPLETED' THEN 1 ELSE 0 END) AS ok, "
            f"AVG(CASE WHEN r.STATUS = 'COMPLETED' AND r.COMPLETED_AT IS NOT NULL AND r.COMPLETED_AT <> '' THEN {dur} END) AS avg_s, "
            "MAX(r.STARTED_AT) AS last_run "
            'FROM cur JOIN "AIW_WORKFLOWS" allv ON allv."ID" = cur."ID" '
            f'LEFT JOIN "AIW_WORKFLOW_RUNS" r ON {run_join} '
            f"WHERE {wsql} "
            'GROUP BY cur."ID", cur."NAME", COALESCE(cur."OWNER", cur."CREATED_BY"), cur."STATUS"'
        )
        cte = f"WITH cur AS ({self._CUR_WORKFLOW}), agg AS ({agg_body}) "
        skip, top = max(0, int(skip)), max(1, int(top))
        rows = self._execute_query(f"{cte} SELECT * FROM agg ORDER BY runs DESC, name ASC LIMIT {top} OFFSET {skip}", p)
        summ = self._execute_query(f"{cte} SELECT COUNT(*) AS matched, SUM(runs) AS total_runs FROM agg", p)[0]
        ranked = self._execute_query(f"{cte} SELECT name, runs FROM agg WHERE runs > 0 ORDER BY runs DESC LIMIT 6", p)
        daily = self._execute_query(
            f"WITH cur AS ({self._CUR_WORKFLOW}) "
            'SELECT SUBSTRING(r.STARTED_AT, 1, 10) AS d, COUNT(*) AS c '
            'FROM cur JOIN "AIW_WORKFLOWS" allv ON allv."ID" = cur."ID" '
            'JOIN "AIW_WORKFLOW_RUNS" r ON r.WORKFLOW_ID = allv."WORKFLOW_ID" '
            # SA-2626: no PARENT_RUN_ID gate — trend counts all runs.
            "AND r.IS_TEST = FALSE AND r.STARTED_AT >= :p1"
            + (" AND r.TENANT_ID = :p0" if tenant else "")
            + f" WHERE {wsql} GROUP BY SUBSTRING(r.STARTED_AT, 1, 10) ORDER BY d", p)
        grand = self._execute_query('SELECT COUNT(DISTINCT "ID") AS c FROM "AIW_WORKFLOWS"')[0]["c"]
        return {
            "rows": rows, "ranked": ranked, "daily": daily,
            "matched": int(summ["matched"] or 0), "total_runs": int(summ["total_runs"] or 0),
            "total": int(grand or 0),
        }

    def _fetch_version(self, run_id: str) -> int | None:
        rows = self._execute_query(
            f"SELECT VERSION FROM {self._table} WHERE ID = :p0", (run_id,),
        )
        if not rows:
            return None
        # HANA returns lowercased keys (see execute_query).
        return int(rows[0].get("version", 0))

    @staticmethod
    def _edge_to_dict(edge: WorkflowEdge) -> dict:
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

    def _context_to_dict(self, ctx: RunContext) -> dict:
        """Serialise a ``RunContext``. **No longer part of the write path** —
        step 5b removed ``CONTEXT`` from ``_DATA_COLUMNS``, and re-adding this to
        ``_data_values`` would write a column that no longer exists.

        Kept because it is still the single place that knows the shape: the
        int-key normalisation test uses it to reproduce a real round-trip, and a
        rollback would need it.
        """
        dag = ctx.topology
        topology_dict = {
            "adjacency": dag.adjacency,
            "predecessors": dag.predecessors,
            "edge_map": {
                node_id: [self._edge_to_dict(e) for e in edges]
                for node_id, edges in dag.edge_map.items()
            },
            "entry_nodes": dag.entry_nodes,
            "leaf_nodes": dag.leaf_nodes,
        } if dag else {}
        return {
            "current_node": ctx.current_node,
            "variables": ctx.variables,
            "loop_state": ctx.loop_state,
            "completed_nodes": list(ctx.completed_nodes),
            "skipped_nodes": list(ctx.skipped_nodes),
            "node_visits": dict(ctx.node_visits),
            "pending_revisits": list(ctx.pending_revisits),
            "child_runs": ctx.child_runs,
            "topology": topology_dict,
            # Routing state for resumed pending nodes: ``process_successors``
            # reads these off the reloaded aggregate to fan out only the
            # taken HUMAN_ACTION branch. Dropping them here meant the
            # worker's reload saw empty maps and traversed ALL edges —
            # confirm AND reject both ran (masked by the aliasing
            # memory repo in tests).
            "last_output_ports": dict(ctx.last_output_ports),
            "last_taken_edge_ports": {
                k: dict(v) for k, v in ctx.last_taken_edge_ports.items()
            },
        }

    # Non-VERSION data columns written on both INSERT and UPDATE paths.
    # Ordered so parameter positions line up for both SQL templates.
    _DATA_COLUMNS = (
        "ID", "WORKFLOW_ID", "WORKFLOW_VERSION", "WORKFLOW_SNAPSHOT_ID",
        # CONTEXT is GONE — step 5b of
        # solace/specs/wcp-node-output-storage-redesign.md. One NCLOB rewritten
        # in full on every save: 192 MB stored, 1.58 GB rewritten across 43,199
        # saves — 8.4x write amplification.
        #
        # Where its contents live now: the projector (variables,
        # completed_nodes, skipped_nodes, child_runs, the port mirrors),
        # AIW_WORKFLOWS (topology), AIW_RUN_LOOP_STATE (loop_state),
        # AIW_RUN_NODE_STATE (node_visits, pending_revisits). current_node was
        # dead — nothing ever read it (§13.5). __credentials is no longer
        # sealed at all (§13.6, phase 1).
        #
        # THIS LIST IS THE WRITE PATH, so removing the name here is what must
        # ship BEFORE the column is dropped. Drop it while this still names it
        # and every INSERT and every CAS UPDATE fails instantly — a total
        # outage, not a degradation.
        "INPUT_DATA", "OUTPUT_DATA", "STATUS",
        "STARTED_AT", "COMPLETED_AT", "ERROR",
        "CORRELATION_ID", "CALLBACK_TOPIC",
        "PARENT_RUN_ID", "PARENT_NODE_ID", "DEPTH_LEVEL", "ROOT_RUN_ID",
        "TENANT_ID", "IS_TEST", "TEST_MODE", "EXECUTION_MODE",
        "GENERATION", "USER_VARIABLES", "METADATA",
        "TRIGGERED_BY",  # SA-2558: server-verified trigger identity
    )

    def _data_values(self, run: WorkflowRun) -> tuple:
        return (
            run.id,
            run.workflow_id,
            run.workflow_version,
            run.workflow_snapshot_id,
            self._to_json(run.input_data),
            self._to_json(run.output_data),
            run.status.value if isinstance(run.status, RunStatus) else run.status,
            run.started_at,
            run.completed_at,
            run.error,
            run.correlation_id,
            run.callback_topic,
            run.parent_run_id,
            run.parent_node_id,
            run.depth_level,
            run.root_run_id,
            run.tenant_id,
            1 if run.is_test else 0,
            run.test_mode,
            run.execution_mode,
            run.generation,
            self._to_json(run.user_variables or {}),
            self._to_json(run.metadata or {}),
            run.triggered_by,  # SA-2558
        )

    def save(self, run: WorkflowRun) -> None:
        """Phase M.2.1 CAS save.

        Splits the former UPSERT into (a) unconditional INSERT when the
        row does not yet exist, (b) conditional
        ``UPDATE ... SET ..., VERSION = :new WHERE ID = :id AND VERSION = :expected``
        when it does. Rowcount 0 on the UPDATE path signals a concurrent
        writer and raises ``OptimisticConcurrencyError``.
        """
        existing_version = self._fetch_version(run.id)
        if existing_version is None:
            # First persist — unconditional insert at VERSION = 1.
            cols = ", ".join(self._DATA_COLUMNS) + ", VERSION"
            placeholders = ", ".join(
                f":p{i}" for i in range(len(self._DATA_COLUMNS) + 1)
            )
            sql = f"INSERT INTO {self._table} ({cols}) VALUES ({placeholders})"
            self._execute_write(sql, self._data_values(run) + (1,))
            run.version = 1
            self._mirror_loop_state(run)
            self._mirror_node_state(run)
            return

        if existing_version != run.version:
            raise OptimisticConcurrencyError(
                run.id, expected=run.version, actual=existing_version,
            )

        new_version = run.version + 1
        # UPDATE sets every data column + VERSION; WHERE is ID + expected VERSION.
        # Column order in SET matches _DATA_COLUMNS after ID (skipped — primary key).
        set_cols = [c for c in self._DATA_COLUMNS if c != "ID"]
        set_clause = ", ".join(f"{c} = :p{i}" for i, c in enumerate(set_cols))
        set_clause += f", VERSION = :p{len(set_cols)}"
        id_param = f":p{len(set_cols) + 1}"
        expected_param = f":p{len(set_cols) + 2}"
        sql = (
            f"UPDATE {self._table} SET {set_clause} "
            f"WHERE ID = {id_param} AND VERSION = {expected_param}"
        )
        # _data_values returns ID first; strip it and append new_version, id, expected.
        data = self._data_values(run)
        params = data[1:] + (new_version, run.id, run.version)
        rows_affected = self._db.execute_write_returning_rowcount(sql, params)
        if rows_affected == 0:
            current = self._fetch_version(run.id)
            raise OptimisticConcurrencyError(
                run.id, expected=run.version,
                actual=current if current is not None else -1,
            )
        run.version = new_version
        self._mirror_loop_state(run)
        self._mirror_node_state(run)

    def _resolve_loop_state(self, run_id: Any, from_context: dict) -> dict:
        """Decide whether a loaded run's ``loop_state`` comes from ``CONTEXT`` or
        from ``AIW_RUN_LOOP_STATE``. Staged like the node-output read.

        ``context`` (default) never queries. ``shadow`` reads both and logs the
        difference but still serves ``CONTEXT``, which is how the divergence
        evidence gets collected before anything depends on it. ``table`` serves
        the rows, falling back to ``CONTEXT`` whenever they are missing.

        The fallback is unconditional rather than clever, because the failure is
        silent in both directions: a run that loads with the wrong ``loop_state``
        does not raise — the loop stalls, or a ``BREAK`` lands on the wrong loop.
        An unrecognised mode value is treated as ``context`` for the same reason
        a typo must not flip a read path.

        Shadow mode is gated on ``CONTEXT`` already having loop state, so the
        6,105 of 6,116 runs that have none pay nothing. Table mode cannot be
        gated that way — after ``DROP CONTEXT`` there is nothing to gate on — so
        it costs one extra SELECT per single-run load. Against a run-continue
        latency floor of 1.75–13.88 s (broker delivery), a point lookup on a
        23-row table is not the thing to optimise; folding it into the run SELECT
        as a LEFT JOIN is the move if that ever stops being true.
        """
        mode = self._loop_state_read_mode
        if mode not in ("shadow", "table") or self._loop_state_repo is None:
            return from_context
        if mode == "shadow" and not from_context:
            return from_context
        if not run_id:
            return from_context
        try:
            from_table = self._loop_state_repo.get_run(str(run_id))
        except Exception:
            logger.warning(
                "loop_state read failed for run %s — serving CONTEXT",
                run_id, exc_info=True)
            return from_context

        if mode == "shadow":
            diffs = _loop_state_differences(from_context, from_table)
            if diffs:
                logger.warning(
                    "loop_state divergence for run %s on %d key(s): %s",
                    run_id, len(diffs), ", ".join(diffs[:12]))
            return from_context
        return from_table if from_table else from_context

    def _mirror_node_state(self, run: WorkflowRun) -> None:
        """Shadow-write ``node_visits`` / ``pending_revisits`` into
        ``AIW_RUN_NODE_STATE``. Same contract as :meth:`_mirror_loop_state`.

        Gated on those two fields being non-empty, which measured on prod-br10 /
        tenant-1 means it fires for **14 and 2 runs out of 6,117** respectively.
        Gating on ``completed_nodes`` instead — which the table also has a column
        for — would invert that: it is non-empty on essentially every run, so the
        mirror would put a delete-plus-N-inserts on every save. That is the write
        amplification this redesign exists to remove, so the event-sourced fields
        ride along with a row but never open one.

        Never raises. Pure shadow write until the read mode is flipped.
        """
        if self._node_state_repo is None or run.context is None:
            return
        if not (getattr(run.context, "node_visits", None)
                or getattr(run.context, "pending_revisits", None)):
            return
        try:
            self._node_state_repo.save_run(run.id, run.context)
        except Exception:
            logger.warning(
                "node_state mirror failed for run %s", run.id, exc_info=True)

    def _resolve_node_state(self, run_id: Any, ctx_data: dict) -> tuple[dict, set]:
        """``(node_visits, pending_revisits)`` — from ``CONTEXT`` or the table.

        Same three stages as :meth:`_resolve_loop_state`, and the same reason for
        the unconditional fallback: losing ``node_visits`` does not raise, it
        re-mints ``{run}-{node}-v0`` and lets the event log's dedupe swallow a
        cycle's second iteration.
        """
        from_context = (
            dict(ctx_data.get("node_visits", {}) or {}),
            set(ctx_data.get("pending_revisits", []) or []),
        )
        mode = self._node_state_read_mode
        if mode not in ("shadow", "table") or self._node_state_repo is None:
            return from_context
        if mode == "shadow" and not (from_context[0] or from_context[1]):
            return from_context
        if not run_id:
            return from_context
        try:
            stored = self._node_state_repo.get_run(str(run_id))
        except Exception:
            logger.warning(
                "node_state read failed for run %s — serving CONTEXT",
                run_id, exc_info=True)
            return from_context

        from_table = (dict(stored.get("node_visits") or {}),
                      set(stored.get("pending_revisits") or set()))
        if mode == "shadow":
            if from_table != from_context:
                # Named keys, not the whole dicts: node_visits is unbounded in
                # node count, and a log line that dumps it is unreadable at
                # exactly the moment someone needs to read it.
                visit_diffs = sorted(
                    k for k in set(from_context[0]) | set(from_table[0])
                    if from_context[0].get(k) != from_table[0].get(k))
                revisit_diffs = sorted(from_context[1] ^ from_table[1])
                logger.warning(
                    "node_state divergence for run %s: visits=%s revisits=%s",
                    run_id, visit_diffs[:12], revisit_diffs[:12])
            return from_context
        # Fall back per FIELD, not per row: a run can legitimately have visits
        # and no revisits, and treating the empty half as "missing" would keep
        # serving a stale CONTEXT for it forever.
        return (from_table[0] or from_context[0],
                from_table[1] or from_context[1])

    def _mirror_loop_state(self, run: WorkflowRun) -> None:
        """Shadow-write ``context.loop_state`` into ``AIW_RUN_LOOP_STATE``.

        ``save`` is the ONE place ``loop_state`` can reach the database — five
        sites mutate it (the two loop drivers, the engine, the two LOOP_EXIT /
        LOOP_CONTINUE handlers) and every one of them relies on this method to
        persist the result. Mirroring here rather than at each mutation site is
        the same chokepoint argument that put ``normalize_loop_state_keys`` in
        ``RunContext.__post_init__``.

        Only fires when the run HAS loop state. 6,105 of 6,116 runs on tenant-1
        do not, and this redesign exists to remove round-trips from the save
        path, so an unconditional DELETE would add 43,199 statements to buy
        nothing. The cost of the gate is that ``loop_state.clear()`` cannot
        propagate from here — ``reset_test_run`` and ``test_full_workflow`` are
        the only two callers that clear it, and they delete their rows directly.

        Never raises. Until the read path is cut over this is a pure shadow
        write, and a shadow write that can fail a run is worse than no shadow
        write at all — the same contract as ``_record_node_output``.
        """
        if self._loop_state_repo is None:
            return
        loop_state = getattr(run.context, "loop_state", None) if run.context else None
        if not loop_state:
            return
        try:
            self._loop_state_repo.save_run(run.id, loop_state)
        except Exception:
            logger.warning(
                "loop_state mirror failed for run %s", run.id, exc_info=True)

    def get_by_id(self, id: str) -> WorkflowRun | None:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0", (id,)
        )
        if not rows:
            return None
        return self._row_to_entity(rows[0], with_run_state=True)

    def get_by_id_version(self, id: str, workflow_version) -> WorkflowRun | None:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE ID = :p0 AND WORKFLOW_VERSION = :p1", (id, workflow_version)
        )
        if not rows:
            return None
        return self._row_to_entity(rows[0], with_run_state=True)

    # ------------------------------------------------------------------
    # Async wrappers — delegate the whole sync method to the connection
    # manager's executor. Keeps the CAS sequence (SELECT VERSION →
    # INSERT/UPDATE WHERE VERSION = expected) coherent on one thread.
    # ------------------------------------------------------------------

    async def aget_by_id(self, id: str) -> WorkflowRun | None:
        return await self._db.arun(self.get_by_id, id)
    
    async def aget_by_id_version(self, id: str, workflow_version: str) -> WorkflowRun | None:
        return await self._db.arun(self.get_by_id_version, id, workflow_version)

    async def asave(self, run: WorkflowRun) -> None:
        await self._db.arun(self.save, run)

    def get_by_root_id(self, root_id: str) -> list[WorkflowRun]:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE ROOT_RUN_ID = :p0", (root_id,)
        )
        return [self._row_to_entity(r) for r in rows]

    def update_status(self, id: str, status: str) -> None:
        self._execute_write(
            f"UPDATE {self._table} SET STATUS = :p0 WHERE ID = :p1",
            (status, id),
        )

    def list_by_workflow(self, workflow_id: str) -> list[WorkflowRun]:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE WORKFLOW_ID = :p0",
            (workflow_id,),
        )
        return [self._row_to_entity(r) for r in rows]

    def delete_all(self) -> int:
        count_rows = self._execute_query(f"SELECT COUNT(*) AS cnt FROM {self._table}")
        count = count_rows[0]["cnt"] if count_rows else 0
        self._execute_write(f"DELETE FROM {self._table}")
        return count

    @staticmethod
    def _rebuild_topology(topology_data: dict) -> GraphTopology:
        edge_map_raw = topology_data.get("edge_map", {})
        edge_map: dict[str, list[WorkflowEdge]] = {}
        for node_id, edges in edge_map_raw.items():
            edge_map[node_id] = [
                WorkflowEdge(
                    id=e.get("id", ""),
                    source_node_id=e.get("source_node_id", ""),
                    target_node_id=e.get("target_node_id", ""),
                    condition=e.get("condition"),
                    source_port_id=e.get("source_port_id", ""),
                    target_port_id=e.get("target_port_id", ""),
                    label=e.get("label", ""),
                    description=e.get("description", ""),
                )
                for e in edges
            ]
        return GraphTopology(
            adjacency=topology_data.get("adjacency", {}),
            predecessors=topology_data.get("predecessors", {}),
            edge_map=edge_map,
            entry_nodes=topology_data.get("entry_nodes", []),
            leaf_nodes=topology_data.get("leaf_nodes", []),
        )

    def _row_to_entity(self, row: dict, with_run_state: bool = False) -> WorkflowRun:
        ctx_data = self._from_json(row.get("context")) or {}

        topology_data = ctx_data.get("topology", {})
        topology = self._rebuild_topology(topology_data) if topology_data else GraphTopology()

        # Off by default because this is called per ROW. ``list_by_workflow`` and
        # ``get_by_root_id`` return many runs to list/aggregate views, and a
        # per-row query there is an N+1 on a path that never touches the loop
        # machinery — every loop handler operates on a run loaded by id.
        loop_state = ctx_data.get("loop_state", {})
        node_visits = dict(ctx_data.get("node_visits", {}) or {})
        pending_revisits = set(ctx_data.get("pending_revisits", []) or [])
        if with_run_state:
            loop_state = self._resolve_loop_state(row.get("id"), loop_state)
            node_visits, pending_revisits = self._resolve_node_state(
                row.get("id"), ctx_data)

        context = RunContext(
            current_node=ctx_data.get("current_node", ""),
            variables=ctx_data.get("variables", {}),
            loop_state=loop_state,
            completed_nodes=set(ctx_data.get("completed_nodes", [])),
            skipped_nodes=set(ctx_data.get("skipped_nodes", [])),
            node_visits=node_visits,
            pending_revisits=pending_revisits,
            child_runs=ctx_data.get("child_runs", {}),
            topology=topology,
            last_output_ports=dict(ctx_data.get("last_output_ports", {}) or {}),
            last_taken_edge_ports={
                k: dict(v)
                for k, v in (ctx_data.get("last_taken_edge_ports", {}) or {}).items()
                if isinstance(v, dict)
            },
        )

        return WorkflowRun(
            id=row["id"],
            workflow_id=row.get("workflow_id", ""),
            workflow_version=row.get("workflow_version", "1"),
            workflow_snapshot_id=row.get("workflow_snapshot_id", "") or "",
            input_data=self._from_json(row.get("input_data")) or {},
            output_data=self._from_json(row.get("output_data")) or {},
            status=RunStatus(row.get("status", "PENDING")),
            context=context,
            started_at=row.get("started_at", ""),
            completed_at=row.get("completed_at"),
            error=row.get("error"),
            correlation_id=row.get("correlation_id", ""),
            callback_topic=row.get("callback_topic", ""),
            parent_run_id=row.get("parent_run_id"),
            parent_node_id=row.get("parent_node_id"),
            depth_level=row.get("depth_level", 0),
            root_run_id=row.get("root_run_id", "") or "",
            tenant_id=row.get("tenant_id", ""),
            is_test=bool(row.get("is_test", 0)),
            test_mode=row.get("test_mode", "") or "",
            execution_mode=row.get("execution_mode", "auto") or "auto",
            version=int(row.get("version", 0) or 0),
            generation=int(row.get("generation", 0) or 0),
            user_variables=self._from_json(row.get("user_variables")) or {},
            metadata=self._from_json(row.get("metadata")) or {},
            triggered_by=row.get("triggered_by"),  # SA-2558
        )

    # ------------------------------------------------------------------
    # list_runs (filtered + paginated)
    # ------------------------------------------------------------------

    _SUMMARY_COLUMNS = (
        "ID", "WORKFLOW_ID", "WORKFLOW_VERSION", "STATUS",
        "STARTED_AT", "COMPLETED_AT", "ERROR",
        "PARENT_RUN_ID", "PARENT_NODE_ID", "DEPTH_LEVEL", "ROOT_RUN_ID",
        "TENANT_ID", "IS_TEST", "CORRELATION_ID", "CALLBACK_TOPIC",
        "TRIGGERED_BY",  # SA-2558: exposed in the runs list for the chip
    )

    _FILTERABLE_COLUMNS = {
        "id": ("ID", str),
        "workflow_id": ("WORKFLOW_ID", str),
        "workflow_version": ("WORKFLOW_VERSION", str),
        "status": ("STATUS", str),
        "parent_run_id": ("PARENT_RUN_ID", str),
        "parent_node_id": ("PARENT_NODE_ID", str),
        "depth_level": ("DEPTH_LEVEL", int),
        "root_run_id": ("ROOT_RUN_ID", str),
        "error": ("ERROR", str),
        "started_at": ("STARTED_AT", str),
        "completed_at": ("COMPLETED_AT", str),
        "tenant_id": ("TENANT_ID", str),
        "correlation_id": ("CORRELATION_ID", str),
        "callback_topic": ("CALLBACK_TOPIC", str),
        "is_test": ("IS_TEST", lambda v: 1 if str(v).lower() in ("true", "1") else 0),
        "test_mode": ("TEST_MODE", str),
        "execution_mode": ("EXECUTION_MODE", str),
        "version": ("VERSION", int),
        "generation": ("GENERATION", int),
        "triggered_by": ("TRIGGERED_BY", str),  # SA-2558: filter runs by trigger identity
    }
    _CONTAINS_COLUMNS = frozenset({
        "id", "workflow_id", "workflow_version", "status",
        "parent_run_id", "parent_node_id", "root_run_id",
        "error", "tenant_id", "correlation_id", "callback_topic",
        "test_mode", "execution_mode", "triggered_by",  # SA-2558
    })
    _SQL_OPS = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}

    def list_runs(self, filters: ListRunsFilter) -> ListRunsPage:
        conditions: list[str] = []
        params: list = []
        idx = 0

        if filters.workflow_id:
            conditions.append(f"WORKFLOW_ID = :p{idx}")
            params.append(filters.workflow_id)
            idx += 1

        if getattr(filters, "workflow_ids", None) is not None:
            if not filters.workflow_ids:
                return ListRunsPage(items=[], total=0)
            placeholders = ", ".join(f":p{idx + i}" for i in range(len(filters.workflow_ids)))
            conditions.append(f"WORKFLOW_ID IN ({placeholders})")
            params.extend(filters.workflow_ids)
            idx += len(filters.workflow_ids)

        if filters.statuses:
            placeholders = ", ".join(f":p{idx + i}" for i in range(len(filters.statuses)))
            conditions.append(f"STATUS IN ({placeholders})")
            params.extend(filters.statuses)
            idx += len(filters.statuses)

        if filters.tenant_id is not None:
            conditions.append(f"TENANT_ID = :p{idx}")
            params.append(filters.tenant_id)
            idx += 1

        if filters.is_test is not None:
            conditions.append(f"IS_TEST = :p{idx}")
            params.append(1 if filters.is_test else 0)
            idx += 1

        if filters.roots_only:
            # Top-level runs only; sub-workflow runs carry a PARENT_RUN_ID.
            conditions.append("(PARENT_RUN_ID IS NULL OR PARENT_RUN_ID = '')")

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

        count_sql = f"SELECT COUNT(*) AS cnt FROM {self._table} WHERE {where}"
        count_rows = self._execute_query(count_sql, tuple(params))
        total = count_rows[0]["cnt"] if count_rows else 0

        col_str = ", ".join(self._SUMMARY_COLUMNS)
        data_sql = (
            f"SELECT {col_str} FROM {self._table} "
            f"WHERE {where} "
            f"ORDER BY STARTED_AT DESC "
            f"LIMIT {filters.limit} OFFSET {filters.offset}"
        )
        rows = self._execute_query(data_sql, tuple(params))
        items = [self._row_to_summary(r) for r in rows]
        return ListRunsPage(items=items, total=total)

    def _row_to_summary(self, row: dict) -> WorkflowRun:
        """Build a lightweight WorkflowRun without parsing heavy JSON context."""
        return WorkflowRun(
            id=row["id"],
            workflow_id=row.get("workflow_id", ""),
            workflow_version=row.get("workflow_version", "1"),
            status=RunStatus(row.get("status", "PENDING")),
            started_at=row.get("started_at", ""),
            completed_at=row.get("completed_at"),
            error=row.get("error"),
            parent_run_id=row.get("parent_run_id"),
            parent_node_id=row.get("parent_node_id"),
            depth_level=row.get("depth_level", 0),
            root_run_id=row.get("root_run_id", "") or "",
            tenant_id=row.get("tenant_id", ""),
            is_test=bool(row.get("is_test", 0)),
            correlation_id=row.get("correlation_id", ""),
            callback_topic=row.get("callback_topic", ""),
            triggered_by=row.get("triggered_by"),  # SA-2558
        )
