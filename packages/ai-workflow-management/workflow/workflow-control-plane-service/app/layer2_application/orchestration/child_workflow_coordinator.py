"""Child-workflow lifecycle — extracted from OrchestrationEngine.

The WORKFLOW node spawns a child run that executes its own DAG and
eventually completes (success) or fails. Three operations cover the
full lifecycle:

* :meth:`start` — when ``_exec_uniform`` hits ``mode == "child_run"``,
  build the child ``WorkflowRun`` from the parent's WORKFLOW node
  config, save it, emit ``ChildRunStarted``, and drive the child's
  entry nodes under its own contextvars.
* :meth:`complete_parent` — when a child reaches ``COMPLETED``,
  propagate its output back to the parent's WORKFLOW node (folding
  through any configured ``output_binding``), publish ``NodeCompleted``
  + ``ChildRunCompleted`` for the parent's tracking task, and continue
  the parent's DAG via ``process_and_dispatch_successors``.
* :meth:`fail_parent` — when a child reaches ``FAILED``, emit
  ``ChildRunFailed`` and branch on the parent WORKFLOW node's
  ``on_failure`` config:
    - ``"skip"``: treat the failure as a success-equivalent
      completion (parent DAG continues via the WORKFLOW node's
      success port).
    - ``"fail"``: mark the parent's tracking task FAILED so
      ``check_run_completion`` propagates the failure upward.

The coordinator delegates orchestration work (publishing events,
applying state updates, dispatching successors, checking run
completion) back to the engine via ``self._engine``. Keeping the
engine reference is the pragmatic alternative to passing six
callbacks; the coordinator is constructed in the engine's ``__init__``
and never escapes it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, NamedTuple

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer2_application.data.data_store_service import MaterializeTooLargeError
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.events import (
    ChildRunCompleted,
    ChildRunFailed,
    ChildRunStarted,
    EventTopic,
    NodeCompleted,
    NodeFailed,
)
from app.layer1_domain.exceptions import (
    CrossWorkflowCycleError,
    WorkflowExecutionError,
)
from app.layer2_application.credentials import CREDENTIALS_KEY
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.definition.workflow_snapshot_loader import capture_snapshot
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.run_save_retry import save_run_with_cas_retry

_logger = logging.getLogger(__name__)

# SA-2458: sentinel value of the WORKFLOW node's environment_id meaning "inherit
# the parent run's $env". Empty environment_id means "No environment" (default);
# a real id selects that environment. Mirrored by the FE EnvironmentPickerControl.
_INHERIT_ENVIRONMENT = "__inherit__"



class _ParentContext(NamedTuple):
    """Resolved (parent_run, parent_workflow, tracking_task) — shared
    pre-flight result for :meth:`complete_parent` and :meth:`fail_parent`.
    """
    parent_run: WorkflowRun
    parent_workflow: Workflow
    tracking_task: Task


class ChildWorkflowCoordinator:
    """Owns the WORKFLOW-node ↔ child-run lifecycle."""

    def __init__(
        self,
        *,
        engine: Any,  # OrchestrationEngine — used for orchestration callbacks
        run_repo: Any,
        task_repo: Any,
        workflow_repo: Any = None,
        workflow_snapshot_repo: Any = None,
        data_store_service: Any = None,
        variable_resolver: Any = None,
        environment_repo: Any = None,
        workflow_environment_repo: Any = None,
        workflow_section_repo: Any = None,
        section_membership_repo: Any = None,
        pool_cursors: Any = None,
    ) -> None:
        self._engine = engine
        self._run_repo = run_repo
        self._task_repo = task_repo
        self._workflow_repo = workflow_repo
        self._workflow_snapshot_repo = workflow_snapshot_repo
        self._data_store_service = data_store_service
        self._variable_resolver = variable_resolver
        # SA-2365: per-subflow environment binding. ``environment_repo`` loads
        # the target env; ``pool_cursors`` gives list-type env vars the same
        # round-robin selection a top-level StartRun uses. Both optional so the
        # dozens of test harnesses that construct the engine still work.
        self._environment_repo = environment_repo
        # SA-2496: assignment repo (AIW_WORKFLOW_ENVIRONMENT) used to GUARD a
        # DYNAMIC (expression-resolved) environment id against the target
        # subflow's SA-2450 assignment. Optional (dormant → never blocks).
        self._workflow_environment_repo = workflow_environment_repo
        # SA-2605: guard a DYNAMIC child env against the sub-flow's SECTION
        # membership (supersedes the SA-2450 subset check above). Optional/dormant.
        self._workflow_section_repo = workflow_section_repo
        self._section_membership_repo = section_membership_repo
        if pool_cursors is None:
            from app.layer2_application.features.start_run.use_cases.env_pool import (
                PoolCursorRegistry,
            )
            pool_cursors = PoolCursorRegistry()
        self._pool_cursors = pool_cursors

    # ------------------------------------------------------------------
    # 1. Start child workflow
    # ------------------------------------------------------------------

    async def start(
        self,
        node: Any,
        parent_task: Task,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        handler_result: Any,
    ) -> None:
        """Launch a child workflow run triggered by a WORKFLOW node."""

        node_state = handler_result.state_updates.get(node.id, {})
        metadata = node_state.get("metadata", {})
        child_workflow_id = metadata.get("child_workflow_id")
        child_input = metadata.get("child_input", {})
        predefined_run_id = metadata.get("predefined_run_id")
        parent_input = metadata.get("parent_input", {})
        # SA-2365: the WORKFLOW node's chosen environment ("" = inherit parent).
        child_environment_id = metadata.get("environment_id", "") or ""
        # SA-2496: whether the environment was chosen dynamically (expression).
        child_environment_dynamic = bool(metadata.get("environment_dynamic", False))

        # Pass file_refs through unchanged. The child run's resolver
        # uses ``FileBackedResolverStrategy``: scalar lookups read the
        # ``extracted`` cache, indexed access uses OData single-row
        # fetch, full-data lookups materialize on demand. Eager
        # materialise here forced a full download even when the child
        # only ever read one field — the regression fix is to delete
        # the loop entirely. The root-keyed materialize cache (see
        # ``FileBackedResolverStrategy._current_cache_key``) means a
        # parent and its children share one bucket, so a file_id
        # already downloaded in the parent never re-downloads in the
        # child.

        if not self._workflow_repo:
            raise WorkflowExecutionError(
                "workflow_repo not configured; cannot start child workflow"
            )

        # Resolve through the same publish-version rule as a top-level
        # ``POST /runs`` (StartRunUseCase): latest PUBLISHED version,
        # falling back to the latest DRAFT. A plain ``aget_by_id`` here
        # let sub-workflows bypass version governance — a child executed
        # whatever row matched the raw id (possibly a stale draft) even
        # when a newer published version existed.
        child_workflow = await self._workflow_repo.aget_by_latest_publish_version(
            child_workflow_id,
        )
        if not child_workflow:
            raise WorkflowExecutionError(
                f"Child workflow '{child_workflow_id}' not found for "
                f"WORKFLOW node '{node.id}'"
            )

        # No pre-run DAG validation for the child either — it runs fan-out
        # driven like any other workflow. The cross-workflow ancestry
        # cycle check stays: it guards against a child re-invoking an
        # ancestor, which the per-run task dedup cannot bound.
        await self._check_ancestry_cycle(run, child_workflow_id)

        # No credential work happens here any more (SA-2287 / B3). A child run
        # carries no credential map; every node inside it fetches its own
        # ``credential_ref`` when IT dispatches, through the same
        # ``RunCredentialProvider`` the root's nodes use, resolving as the root
        # run's identity. That is child-run parity (B7 / SA-2291) achieved by
        # having nothing here to keep in step, rather than by a second copy of
        # the resolution logic.
        child_topology = GraphTopology.from_workflow(child_workflow)

        # Merge parent workflow input as base, then overlay explicit
        # input_binding. Lets the child access all parent input fields
        # even when input_binding only maps a subset.
        merged_input = dict(parent_input)
        merged_input.update(child_input)
        child_input = merged_input

        # Store child input as CSV file (always, even if empty, for
        # consistent file_ref format).
        child_input_var: Any = child_input or {}
        if self._data_store_service:
            child_input_var = await self._data_store_service.store_data(
                "input", child_input_var,
            )

        # Capture an immutable snapshot of the child workflow for this
        # run. Subsequent edits to the live workflow won't leak into the
        # child run — same guarantee parent runs get from
        # ``StartRunUseCase``.
        child_snapshot_id = capture_snapshot(
            child_workflow, self._workflow_snapshot_repo,
        )

        child_run = WorkflowRun(
            workflow_id=child_workflow_id,
            workflow_version=child_workflow.version,
            workflow_snapshot_id=child_snapshot_id,
            status=RunStatus.PENDING,
            input_data=child_input,
            context=RunContext(
                variables={"input": child_input_var, "artifacts": {}},
                topology=child_topology,
            ),
            parent_run_id=run.id,
            parent_node_id=node.id,
            depth_level=run.depth_level + 1,
            root_run_id=run.root_run_id or run.id,
            tenant_id=run.tenant_id,
            correlation_id=run.correlation_id,
        )

        # Apply predefined run ID so the frontend can subscribe to
        # ``run:{child_run_id}`` before INPUT nodes fire InputRequested
        # events.
        if predefined_run_id:
            child_run.id = predefined_run_id

        # SA-2365: bind the child run's ``$env``. A non-empty environment_id on
        # the WORKFLOW node selects a SPECIFIC environment (so one parent flow
        # can drive the same subflow against dev on one branch and qas on
        # another); empty inherits the parent run's already-bound $env. Done
        # before the persist below so the source travels on the child's context
        # whether the child is driven inline or off ChildRunStarted (executor).
        self._bind_child_environment(
            child_run, run, child_environment_id,
            is_dynamic=child_environment_dynamic,
        )

        # Register child in parent context.
        run.context.child_runs[node.id] = child_run.id

        _new_child_run_id = child_run.id
        _parent_node_id = node.id

        def _apply_register_child(r: WorkflowRun) -> None:
            r.context.child_runs[_parent_node_id] = _new_child_run_id

        saved = await save_run_with_cas_retry(
            self._run_repo, run.id, _apply_register_child,
        )
        if saved is not None:
            # The CAS save reloaded a fresh row and bumped the persisted
            # version; mirror it onto the in-memory ``run`` the engine
            # still holds so ``_execute_node_inner``'s trailing
            # ``asave(run)`` doesn't conflict. (The version is
            # intentionally bumped twice per child trigger: here and at
            # that trailing save.)
            run.version = saved.version
        # child_run is a brand-new entity; first persist is unconditional
        # INSERT at version 1 — no CAS conflict possible.
        await self._run_repo.asave(child_run)

        # Emit ChildRunStarted BEFORE handle_entry_nodes so the frontend
        # can subscribe to ``run:{child_run_id}`` before InputRequested
        # fires.
        self._engine.publish_event(
            EventTopic.CHILD_RUN_STARTED,
            ChildRunStarted(
                parent_run_id=run.id,
                parent_node_id=node.id,
                child_run_id=child_run.id,
                child_workflow_id=child_workflow_id,
                root_run_id=run.root_run_id or run.id,
                depth=run.depth_level,
                source="workflow-control-plane",
            ),
        )

        # Execute child entry nodes under the child's own run_id
        # contextvar so the file-backed resolver caches are keyed by
        # the child run (not the parent) and drain_events/drain_events
        # fire inside the child's scope. Mirrors
        # ``_orchestrate_start_inner`` so the child-workflow path is
        # semantically equivalent to a standalone run.
        # Fix C for the 2026-04-11 child-workflow bug — see
        # docs/design/child-workflow-input-bug-analysis.md §Finding 3.
        child_task_factory = TaskFactory()
        child_tokens = self._engine._enter_run_scope(child_run.id, run=child_run)
        try:
            # Phase 4 Sub 4.2 — under EXECUTOR_MODE=executor the
            # ``RunStartConsumer`` drives child entry-node dispatch
            # off the ``ChildRunStarted`` event emitted above. Engine
            # skips the inline walk to avoid double dispatch.
            if not self._engine._is_executor_mode():
                await self._engine.handle_entry_nodes(
                    child_topology.entry_nodes, child_run, child_workflow,
                    child_topology, child_task_factory,
                )

            child_run.status = RunStatus.RUNNING
            if not child_run.started_at:
                child_run.started_at = datetime.now(timezone.utc).isoformat()
            await self._run_repo.asave(child_run)

            # Drain any events buffered during the child's entry-node
            # pass BEFORE check_run_completion runs, so the frontend
            # sees the child's InputRequested / EdgeTraversed events in
            # priority order rather than interleaved with the parent's
            # subsequent events.
            await self._engine.drain_events()

            # Parity with ``StartRunUseCase``: land the child's input into
            # the event log / projector so a view-based read of the child
            # run sees ``$input`` (and the run's ``input_data``), not only
            # the aggregate. Two projector fields, two events:
            #   * ``input_data``      ← RunStatusChanged(status="started")
            #   * ``variables.input`` ← NodeOutputProduced(__bootstrap__)
            # Placed AFTER drain (the IMMEDIATE entry-node ``node.dispatched``
            # events are already flushed, so these NORMAL events keep the
            # priority-lane order the child-workflow event-order tests pin)
            # and BEFORE check_run_completion (whose "pending" / "completed"
            # RunStatusChanged must follow "started" so the projected status
            # ends correct).
            self._engine.emit_run_status(
                child_run, "started", input_data=child_run.input_data or {},
            )
            # ``__credentials`` is filtered out for the same reason
            # ``StartRunUseCase`` filters it out of its own seed event: the
            # map is never published, encrypted or not. Every other emitter
            # that ships variables or resolved_input does the same.
            self._engine.emit_variables_seeded(
                child_run,
                {
                    k: v for k, v in child_run.context.variables.items()
                    if k != CREDENTIALS_KEY
                },
            )

            await self._engine.check_run_completion(child_run, child_workflow)
            await self._engine.drain_events()
        finally:
            self._engine._exit_run_scope(child_tokens)

    def _bind_child_environment(
        self, child_run: WorkflowRun, parent_run: WorkflowRun, environment_id: str,
        *, is_dynamic: bool = False,
    ) -> None:
        """Populate the child run's ``$env`` source (SA-2365 / SA-2458 / SA-2496).

        Driven by the WORKFLOW node's Environment picker:

        * ``"__inherit__"`` → inherit the parent run's already-resolved ``$env``
          unchanged (the child sees exactly what the parent sees).
        * a real environment id → load THAT environment and bind its non-secret
          ``$env`` (same ``load_and_bind`` a top-level StartRun uses, so list
          pools round-robin and credential-type vars carry their ref id).
        * empty (the DEFAULT) → **No environment**: the child runs with no
          ``$env`` at all — nothing is bound, so ``{{$env.KEY}}`` inside the
          subflow stays unresolved unless the subflow selects its own env.

        SA-2496 "Dynamic environment (only id)": when ``is_dynamic`` (the picker
        chose Dynamic and the value was an expression resolved by the handler),
        the resolved value MUST be an environment **id** in the target subflow's
        SA-2450 assignment. A value that is not an assigned id — a name/label, a
        wrong id, or any env for an unassigned subflow — fails the node
        (ENVIRONMENT_NOT_ASSIGNED); it never silently runs against the wrong env.
        A literal id (non-dynamic) keeps its existing SA-2458 behavior.
        """
        environment_id = (environment_id or "").strip()
        if environment_id == _INHERIT_ENVIRONMENT:
            parent_env = parent_run.context.variables.get("env")
            if parent_env is not None:
                child_run.context.variables["env"] = parent_env
            return
        # SA-2605 precedence: if the sub-flow is bound to a section, guard the
        # dynamic env against section membership and SKIP the legacy SA-2450 check.
        _child_section_bound = False
        if environment_id and is_dynamic and self._workflow_section_repo is not None:
            try:
                _cb = self._workflow_section_repo.get_by_workflow(
                    tenant_id=child_run.tenant_id, workflow_id=child_run.workflow_id
                )
                _child_section_bound = _cb is not None and bool((_cb.section or "").strip())
            except Exception:
                _child_section_bound = False
        if (
            environment_id
            and is_dynamic
            and not _child_section_bound
            and self._workflow_environment_repo is not None
        ):
            from app.layer2_application.features.start_run.use_cases.env_assignment_guard import (
                check_environment_assignment,
            )
            reject = check_environment_assignment(
                repo=self._workflow_environment_repo,
                tenant_id=child_run.tenant_id,
                workflow_id=child_run.workflow_id,
                environment_id=environment_id,
            )
            if reject:
                raise WorkflowExecutionError(
                    f"ENVIRONMENT_NOT_ASSIGNED — dynamic environment "
                    f"'{environment_id}' for sub-workflow "
                    f"'{child_run.workflow_id}': {reject}"
                )
        # SA-2605: a DYNAMIC env must also be a member of the sub-flow's SECTION
        # (the new model). Dormant when the sub-flow has no section binding.
        if (
            environment_id
            and is_dynamic
            and self._workflow_section_repo is not None
            and self._section_membership_repo is not None
        ):
            from app.layer2_application.features.start_run.use_cases.section_run_guard import (
                check_section_run,
            )
            section_reject = check_section_run(
                workflow_section_repo=self._workflow_section_repo,
                section_membership_repo=self._section_membership_repo,
                tenant_id=child_run.tenant_id,
                workflow_id=child_run.workflow_id,
                environment_id=environment_id,
            )
            if section_reject:
                raise WorkflowExecutionError(
                    f"ENVIRONMENT_NOT_IN_SECTION — dynamic environment "
                    f"'{environment_id}' for sub-workflow "
                    f"'{child_run.workflow_id}': {section_reject}"
                )
        if environment_id and self._environment_repo is not None:
            from app.layer2_application.features.start_run.use_cases.env_pool import (
                load_and_bind,
            )
            # SA-2496: pass the module's stdlib ``_logger``. ``structlog`` is NOT a
            # control-plane dependency (importing it here crashed with "No module
            # named 'structlog'"), and ``load_and_bind`` now logs plain messages,
            # so a stdlib logger is sufficient.
            load_and_bind(
                child_run,
                tenant_id=child_run.tenant_id,
                environment_id=environment_id,
                repo=self._environment_repo,
                cursors=self._pool_cursors,
                logger=_logger,
            )
            return
        # empty → No environment: leave the child's $env unset.

    # ------------------------------------------------------------------
    # 2. Complete parent tracking task (child reached COMPLETED)
    # ------------------------------------------------------------------

    async def complete_parent(self, child_run: WorkflowRun) -> None:
        """Resume parent DAG after a child run completes successfully.

        Serialized under the PARENT run's lock: this path is entered
        from the child's orchestration (child lock held, or no lock for
        broker consumers), and the parent-side work below mutates parent
        state — ``node_visits`` bumps, ``completed_nodes``, successor
        task minting — that must not interleave with the parent's own
        orchestration. ``locked_run`` is reentrant, so the synchronous
        path (child started AND completed inline under the parent's
        already-held lock in :meth:`start`) re-enters instead of
        deadlocking. Acquisition is strictly child → parent (upward),
        and the ancestry-cycle check keeps that acyclic.
        """
        if not child_run.parent_run_id:
            return
        async with self._engine.locked_run(child_run.parent_run_id):
            await self._complete_parent_locked(child_run)

    async def _complete_parent_locked(self, child_run: WorkflowRun) -> None:
        ctx = await self._resolve_parent_context(child_run)
        if ctx is None:
            return
        parent_run, parent_workflow, tracking_task = ctx
        if tracking_task.status == TaskStatus.COMPLETED:
            return  # Idempotent

        # Build child output and store in parent variables.
        # Materialize file refs so the parent receives actual data, not
        # opaque file-reference dicts nested inside another file ref.
        #
        # NOTE (SA-1905 D2.2 reverted): an earlier attempt removed this
        # materialize to pass a file_ref pointer straight through. That broke
        # the sub-workflow OUTPUT contract in production: with a data-store
        # wired, an OUTPUT/COMPUTE node's ``{"output": ..., "metadata": ...}``
        # envelope is stored as a BARE file_ref, so ``child_run.output_data[k]``
        # is a file_ref whose keys are ``__file_ref/file_id/columns/...`` with
        # no ``"output"`` key. The materialize here is load-bearing: it UNWRAPS
        # that envelope (materialize(bare_ref) -> ``{"output": ..., "metadata":
        # ...}``) so the field-lift (below) and OUTPUT-contract merge see the
        # real fields. Removing it makes ``flat.update(<file_ref>)`` spread the
        # ref metadata as parent fields and ``{{$wf.output.result}}`` raise
        # "Key 'output' not found". Covered by tests/integration/
        # test_output_node_contract.py. Lazy envelope-unwrap for large child
        # outputs is deferred to D2.4.
        raw_output = child_run.output_data or {}
        if self._data_store_service and isinstance(raw_output, dict):
            try:
                materialized_output: dict[str, Any] = {}
                for k, v in raw_output.items():
                    if is_file_ref(v):
                        # SA-1942 — size-guarded: unwrapping the child's envelope
                        # here materializes each field into PARENT RAM. A small
                        # envelope (``{"output": <pointer>, "metadata": …}``)
                        # stays under the cap and unwraps as before; an oversized
                        # inline field is refused rather than OOMing the parent.
                        # Lazy envelope-unwrap for large child outputs remains the
                        # deferred D2.4 follow-up.
                        materialized_output[k] = await self._data_store_service.materialize_guarded(
                            v, context=f"child-output.{k}",
                        )
                    else:
                        materialized_output[k] = v
                raw_output = materialized_output
            except MaterializeTooLargeError as exc:
                # The child's output is too large to relay inline. Fail the
                # parent WORKFLOW node CLEANLY (mark the tracking task FAILED +
                # NODE_FAILED) so ``check_run_completion`` propagates the failure
                # up the chain — instead of letting the raise crash the
                # orchestration worker mid-drain.
                await self._fail_parent_relay_too_large(
                    child_run, parent_run, parent_workflow, tracking_task, exc,
                )
                return

        # Expose every sub-workflow node's output under the WORKFLOW node so
        # the parent can address them with a single ``.output`` hop:
        #   {{$<wf-node>.output.<child-node-id>.<field>}}
        # ``child_run.output_data`` is keyed by child node id, each value the
        # node's variable entry. Two entry shapes exist:
        #   * Worker (TASK) nodes — ``CompleteTaskUseCase`` stores ``output``
        #     as a thin ``{"uri": <ref>}`` file pointer and spreads the real
        #     worker fields (response_body, status_code, …) at the entry's
        #     TOP level. Lifting ``output`` here would expose only the uri,
        #     dropping the worker output, so ``$<wf-node>.output.<child>.
        #     response_body`` can't resolve. Lift the top-level fields instead.
        #   * INPUT / pending / nested-WORKFLOW nodes — the data lives under
        #     ``output`` (``{"output": ..., "metadata": ...}``); lift ``output``
        #     so a child node's fields sit directly under its id.
        # Non-wrapped entries (e.g. the child's ``input`` ref) pass through.
        node_outputs: dict[str, Any] = {}
        if isinstance(raw_output, dict):
            for node_key, entry in raw_output.items():
                if isinstance(entry, dict) and "output" in entry:
                    worker_fields = {
                        k: v for k, v in entry.items()
                        if k not in ("output", "metadata")
                    }
                    node_outputs[node_key] = worker_fields or entry["output"]
                else:
                    node_outputs[node_key] = entry

        # OUTPUT contract: when the child workflow defines OUTPUT node(s)
        # and at least one produced a dict entry, those resolved fields ARE
        # the child's output — merged flat so the parent addresses them as
        # ``{{$<wf-node>.output.<field>}}`` with no node-id hop. Multiple
        # OUTPUT nodes merge in definition order (later wins). With no
        # OUTPUT node (or all skipped on untaken branches, or no usable
        # snapshot), keep the keyed-by-node-id shape above. Note this also
        # narrows what a parent-side ``output_binding`` can reference:
        # ``{{child.<non-output-node>.x}}`` no longer resolves once the
        # child declares an OUTPUT node — the OUTPUT schema is the contract.
        output_node_ids: set[str] = set()
        child_workflow = None
        if self._workflow_snapshot_repo:
            try:
                child_workflow = self._engine.load_workflow_for_run(child_run)
                output_node_ids = {
                    n.id for n in child_workflow.nodes
                    if n.node_type == NodeType.OUTPUT
                }
            except Exception:
                output_node_ids = set()
        if output_node_ids:
            flat: dict[str, Any] = {}
            merged_any = False
            for node in child_workflow.nodes:
                if node.id in output_node_ids and isinstance(
                    node_outputs.get(node.id), dict,
                ):
                    flat.update(node_outputs[node.id])
                    merged_any = True
            if merged_any:
                node_outputs = flat

        child_output = {
            "output": node_outputs,
            "metadata": {
                "child_run_id": child_run.id,
                "child_workflow_id": child_run.workflow_id,
                "status": child_run.status.value,
            },
        }

        # Apply output_binding if configured.
        node_state = self._engine._view_variables(parent_run).get(
            child_run.parent_node_id, {},
        )
        output_binding = (
            node_state.get("metadata", {}) if isinstance(node_state, dict) else {}
        ).get("output_binding", {})
        if not output_binding:
            # child_run mode defers state_updates publication, so the
            # dispatch-time metadata (where the WORKFLOW handler relays
            # ``output_binding``) never reaches the parent's view — fall
            # back to the design-time source of truth: the node config.
            parent_node = parent_workflow.get_node(child_run.parent_node_id)
            if parent_node:
                output_binding = parent_node.config.get("output_binding", {}) or {}
        if output_binding and self._variable_resolver and child_run.output_data:
            # UI may send output_binding as a list of {param_name, param_value};
            # ``resolve_mapping`` expects a flat dict {key: expression}.
            if isinstance(output_binding, list):
                output_binding = {
                    item["param_name"]: item["param_value"]
                    for item in output_binding
                    if isinstance(item, dict) and "param_name" in item and "param_value" in item
                }
            resolved_output = await self._variable_resolver.resolve_mapping(
                output_binding, {"child": raw_output},
            )
            child_output["output"].update(resolved_output)

        # Route parent-variable write-set through
        # ``apply_state_updates_as_files`` so the projector sees a
        # ``NodeOutputProduced`` for the parent's WORKFLOW node when a
        # child completes.
        await self._engine.apply_state_updates_as_files(
            {child_run.parent_node_id: child_output},
            parent_run,
            parent_workflow,
            source_node_id=child_run.parent_node_id,
            causation_id=tracking_task.id,
            output_port="success",
            attempt_id=tracking_task.idempotency_key or "",
        )

        # Mark tracking task completed.
        tracking_task.status = TaskStatus.COMPLETED
        tracking_task.completed_at = datetime.now(timezone.utc).isoformat()
        await self._task_repo.asave(tracking_task)

        # Mark node completed in run context BEFORE publishing
        # NodeCompleted so downstream consumers reading the aggregate
        # see the parent in its post-completion state.
        parent_run.context.completed_nodes.add(child_run.parent_node_id)

        # Publish NodeCompleted + ChildRunCompleted BEFORE successor
        # dispatch so both stamp lower per-run sequence numbers than any
        # successor event the subsequent fan-out emits.
        self._engine.publish_event(
            EventTopic.NODE_COMPLETED,
            NodeCompleted(
                task_id=tracking_task.id,
                run_id=parent_run.id,
                workflow_id=parent_run.workflow_id,
                node_id=child_run.parent_node_id,
                root_run_id=parent_run.root_run_id or parent_run.id,
                depth=parent_run.depth_level,
                source="workflow-control-plane",
            ),
        )
        self._engine.publish_event(
            EventTopic.CHILD_RUN_COMPLETED,
            ChildRunCompleted(
                parent_run_id=parent_run.id,
                parent_node_id=child_run.parent_node_id,
                child_run_id=child_run.id,
                child_workflow_id=child_run.workflow_id,
                root_run_id=parent_run.root_run_id or parent_run.id,
                depth=parent_run.depth_level,
                source="workflow-control-plane",
            ),
        )

        # Node-by-node test isolation: the tracking task is already COMPLETED
        # and NodeCompleted/ChildRunCompleted are published above — all a
        # single-node test needs. Skip successor dispatch AND the
        # run-completion check: ``process_and_dispatch_successors`` runs
        # ``check_run_completion(parent_run)`` unconditionally (its
        # ``test_mode != "node"`` guard only wraps dispatch), which would flip
        # the PERMANENT test run to COMPLETED and re-poison the projector.
        if parent_run.test_mode == "node":
            await self._engine.drain_events()
            await self._engine.drain_events()
        else:
            # Delegate successor processing + dispatch + check_run_completion
            # + flush to the engine helper.
            await self._engine.process_and_dispatch_successors(
                tracking_task, parent_run, parent_workflow,
                parent_run.context.topology,
            )
        # A WORKFLOW (sub-workflow) node can itself be a LOOP body. The
        # tracking task carries the loop scope_stack, so advance the parent's
        # event-driven loop the same way worker/inline body completions do
        # (orchestrate_continue / orchestrate_execute_node). Without this a
        # loop whose body is a sub-workflow stalls at iteration 0. No-op for a
        # non-loop-body WORKFLOW node (terminal-status + scope gate).
        await self._engine.maybe_enqueue_loop_iteration(tracking_task, parent_run)

    # ------------------------------------------------------------------
    # 3. Fail parent tracking task (child reached FAILED)
    # ------------------------------------------------------------------

    async def fail_parent(self, child_run: WorkflowRun) -> None:
        """Propagate child failure to parent run.

        Same parent-lock discipline as :meth:`complete_parent` — the
        ``on_failure=skip`` branch routes parent successors and the fail
        branch fails the parent's tracking task, both parent-state
        mutations.
        """
        if not child_run.parent_run_id:
            return
        async with self._engine.locked_run(child_run.parent_run_id):
            await self._fail_parent_locked(child_run)

    async def _fail_parent_locked(self, child_run: WorkflowRun) -> None:
        ctx = await self._resolve_parent_context(
            child_run,
            terminal_task_statuses=(
                TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED,
            ),
        )
        if ctx is None:
            return
        parent_run, parent_workflow, tracking_task = ctx
        parent_node = parent_workflow.get_node(child_run.parent_node_id)
        if parent_node is None:
            return

        # Announce the child run's failure (parent-scoped) regardless of
        # the parent's ``on_failure`` policy — the event describes the
        # child outcome, not the parent's routing decision.
        self._engine.publish_event(
            EventTopic.CHILD_RUN_FAILED,
            ChildRunFailed(
                parent_run_id=parent_run.id,
                parent_node_id=child_run.parent_node_id,
                child_run_id=child_run.id,
                child_workflow_id=child_run.workflow_id,
                error=child_run.error or "",
                root_run_id=parent_run.root_run_id or parent_run.id,
                depth=parent_run.depth_level,
                source="workflow-control-plane",
            ),
        )

        on_failure = parent_node.config.get("on_failure", "fail")

        if on_failure == "skip":
            await self._fail_parent_skip_branch(
                child_run, parent_run, parent_workflow, tracking_task,
            )
        else:
            await self._fail_parent_fail_branch(
                child_run, parent_run, parent_workflow, tracking_task,
            )

    async def _fail_parent_skip_branch(
        self,
        child_run: WorkflowRun,
        parent_run: WorkflowRun,
        parent_workflow: Workflow,
        tracking_task: Task,
    ) -> None:
        """``on_failure=skip`` — treat the child failure as a success-
        equivalent completion so the parent's success-port routing
        continues. The parent isn't told the child failed (other than
        via ``ChildRunFailed`` which already fired)."""
        tracking_task.status = TaskStatus.COMPLETED
        tracking_task.output_metadata = {
            "skipped": True, "reason": "child_workflow_failed",
        }
        tracking_task.completed_at = datetime.now(timezone.utc).isoformat()
        await self._task_repo.asave(tracking_task)

        # Phase 2b Gap 2 — emit NodeOutputProduced + NodeCompleted
        # BEFORE mutating the aggregate so the projector + dispatcher
        # see the WORKFLOW-node completion. ``output_port="success"``
        # because downstream routing treats a skipped child as
        # success-equivalent.
        skipped_output = {
            "output": {},
            "metadata": {
                "child_run_id": child_run.id,
                "child_workflow_id": child_run.workflow_id,
                "status": child_run.status.value,
                "skipped": True,
                "reason": "child_workflow_failed",
            },
        }
        await self._engine.apply_state_updates_as_files(
            {child_run.parent_node_id: skipped_output},
            parent_run,
            parent_workflow,
            source_node_id=child_run.parent_node_id,
            causation_id=tracking_task.id,
            output_port="success",
            attempt_id=tracking_task.idempotency_key or "",
        )

        parent_run.context.completed_nodes.add(child_run.parent_node_id)
        parent_run.context.skipped_nodes.add(child_run.parent_node_id)

        self._engine.publish_event(
            EventTopic.NODE_COMPLETED,
            NodeCompleted(
                task_id=tracking_task.id,
                run_id=parent_run.id,
                workflow_id=parent_run.workflow_id,
                node_id=child_run.parent_node_id,
                root_run_id=parent_run.root_run_id or parent_run.id,
                depth=parent_run.depth_level,
                source="workflow-control-plane",
            ),
        )

        # Node-test isolation: see ``complete_parent`` — skip successor
        # dispatch + the unconditional ``check_run_completion`` that would
        # flip the permanent test run terminal; drain + flush only.
        if parent_run.test_mode == "node":
            await self._engine.drain_events()
            await self._engine.drain_events()
        else:
            await self._engine.process_and_dispatch_successors(
                tracking_task, parent_run, parent_workflow,
                parent_run.context.topology,
            )
        # on_failure="skip" treats the child as success-equivalent — advance the
        # parent loop if this sub-workflow node is a loop body (same as the
        # success branch above). No-op otherwise.
        await self._engine.maybe_enqueue_loop_iteration(tracking_task, parent_run)

    async def _fail_parent_fail_branch(
        self,
        child_run: WorkflowRun,
        parent_run: WorkflowRun,
        parent_workflow: Workflow,
        tracking_task: Task,
    ) -> None:
        """``on_failure=fail`` — mark the tracking task FAILED so
        ``check_run_completion`` propagates the failure up the chain."""
        # Name the child workflow (not its UUID) in the propagated message;
        # ``child_run.error`` already carries the failing node name + real
        # cause (set in ``check_run_completion`` / ``orchestrate_fail``).
        child_workflow = (
            self._engine.load_workflow_for_run(child_run)
            if self._workflow_snapshot_repo
            else None
        )
        child_label = (
            child_workflow.name
            if child_workflow and child_workflow.name
            else child_run.workflow_id
        )
        error_msg = (
            f"Child workflow '{child_label}' failed: "
            f"{child_run.error or 'unknown'}"
        )
        tracking_task.status = TaskStatus.FAILED
        tracking_task.error = error_msg
        tracking_task.completed_at = datetime.now(timezone.utc).isoformat()
        await self._task_repo.asave(tracking_task)

        self._engine.publish_event(
            EventTopic.NODE_FAILED,
            NodeFailed(
                task_id=tracking_task.id,
                run_id=parent_run.id,
                workflow_id=parent_run.workflow_id,
                node_id=child_run.parent_node_id,
                error_code="CHILD_WORKFLOW_FAILED",
                error_message=error_msg,
                root_run_id=parent_run.root_run_id or parent_run.id,
                depth=parent_run.depth_level,
                source="workflow-control-plane",
            ),
        )

        await self._engine.drain_events()
        # Node-test isolation: a failed child surfaces the WORKFLOW node as
        # FAILED (NodeFailed above) without flipping the permanent test run to
        # FAILED — skip the run-completion check for node-tests.
        if parent_run.test_mode != "node":
            await self._engine.check_run_completion(parent_run, parent_workflow)
        # Flush so the parent's RUN_COMPLETED / RUN_FAILED reaches the wire.
        await self._engine.drain_events()

    async def _fail_parent_relay_too_large(
        self,
        child_run: WorkflowRun,
        parent_run: WorkflowRun,
        parent_workflow: Workflow,
        tracking_task: Task,
        exc: MaterializeTooLargeError,
    ) -> None:
        """SA-1942 — a child SUCCEEDED but its output can't be relayed into the
        parent without breaching the materialize cap. Fail the parent WORKFLOW
        node cleanly (mirrors ``_fail_parent_fail_branch``) so the failure
        propagates as a normal RUN_FAILED instead of an unhandled exception
        crashing the orchestration worker mid-drain."""
        # Idempotent under at-least-once redelivery: a redelivered
        # ChildRunCompleted re-enters _complete_parent_locked and re-raises here
        # while the parent is not yet terminal; the COMPLETED-only guard at the
        # top of _complete_parent_locked misses a FAILED tracking task, so
        # short-circuit before double-firing NODE_FAILED / check_run_completion.
        if tracking_task.status == TaskStatus.FAILED:
            return
        error_msg = f"Child workflow output could not be relayed to the parent: {exc}"
        tracking_task.status = TaskStatus.FAILED
        tracking_task.error = error_msg
        tracking_task.completed_at = datetime.now(timezone.utc).isoformat()
        await self._task_repo.asave(tracking_task)

        self._engine.publish_event(
            EventTopic.NODE_FAILED,
            NodeFailed(
                task_id=tracking_task.id,
                run_id=parent_run.id,
                workflow_id=parent_run.workflow_id,
                node_id=child_run.parent_node_id,
                error_code="CHILD_OUTPUT_TOO_LARGE",
                error_message=error_msg,
                root_run_id=parent_run.root_run_id or parent_run.id,
                depth=parent_run.depth_level,
                source="workflow-control-plane",
            ),
        )
        await self._engine.drain_events()
        if parent_run.test_mode != "node":
            await self._engine.check_run_completion(parent_run, parent_workflow)
        await self._engine.drain_events()

    # ------------------------------------------------------------------
    # Shared pre-flight
    # ------------------------------------------------------------------

    async def _resolve_parent_context(
        self,
        child_run: WorkflowRun,
        terminal_task_statuses: tuple = (TaskStatus.COMPLETED,),
    ) -> _ParentContext | None:
        """4-step guard chain shared by :meth:`complete_parent` and
        :meth:`fail_parent`. Returns ``None`` whenever the parent
        cannot be resumed (run gone, run already terminal, workflow
        snapshot missing, tracking task missing or already terminal).
        """
        parent_run = await self._run_repo.aget_by_id(child_run.parent_run_id)
        terminal_run = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
        if not parent_run or parent_run.status in terminal_run:
            return None

        parent_workflow = (
            self._engine.load_workflow_for_run(parent_run)
            if self._workflow_snapshot_repo
            else None
        )
        if not parent_workflow:
            return None

        if not parent_workflow.get_node(child_run.parent_node_id):
            return None

        tracking_task = await self._find_tracking_task(
            parent_run.id, child_run.parent_node_id,
        )
        if not tracking_task or tracking_task.status in terminal_task_statuses:
            return None

        return _ParentContext(parent_run, parent_workflow, tracking_task)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _check_ancestry_cycle(
        self, run: WorkflowRun, child_workflow_id: str,
    ) -> None:
        """Walk parent_run_id chain to detect cross-workflow cycles."""
        visited_workflow_ids = {run.workflow_id}
        current = run
        while current.parent_run_id:
            parent = await self._run_repo.aget_by_id(current.parent_run_id)
            if not parent:
                _logger.warning(
                    "Parent run %s not found during ancestry cycle check "
                    "for child workflow %s — cycle detection may be incomplete",
                    current.parent_run_id, child_workflow_id,
                )
                break
            if (
                parent.workflow_id in visited_workflow_ids
                and parent.workflow_id == child_workflow_id
            ):
                raise CrossWorkflowCycleError(
                    f"Cross-workflow cycle detected: workflow "
                    f"'{child_workflow_id}' already appears in the ancestry chain"
                )
            visited_workflow_ids.add(parent.workflow_id)
            current = parent
        if child_workflow_id in visited_workflow_ids:
            raise CrossWorkflowCycleError(
                f"Cross-workflow cycle detected: workflow "
                f"'{child_workflow_id}' already appears in the ancestry chain"
            )

    async def _find_tracking_task(
        self, run_id: str, node_id: str,
    ) -> Task | None:
        """Find the tracking task for a WORKFLOW node in a given run.

        Returns the LATEST task for the node, not the first match: an
        SA-846 revisit leaves the visit-0 tracking task COMPLETED next to
        the in-flight ``-v{n}`` one, and returning the stale terminal task
        made ``_resolve_parent_context`` bail — the revisit's child run
        could then never complete its parent. Ordering mirrors
        ``OrchestrationEngine._active_task`` (created_at, with the
        visit-suffixed id as tiebreaker for same-timestamp saves).
        """
        candidates = [
            t for t in await self._task_repo.alist_by_run(run_id)
            if t.node_id == node_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda t: ((t.created_at or ""), t.id))
