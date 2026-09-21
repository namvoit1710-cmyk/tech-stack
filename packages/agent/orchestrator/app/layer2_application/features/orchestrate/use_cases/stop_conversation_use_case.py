"""Stop a conversation — fan a Stop out to a registered set of stop targets.

SA-892. A single Stop fans out to several downstream targets:

* cancel the CURRENT MESSAGE's in-flight tasks (task-scoped stop),
* cancel the agent turn via an ``agent.control`` message,
* stop every in-flight workflow run (dispatch a ``workflow_stop`` step to the
  executor, which performs the control-plane cancel).

Each target is a ``StopTarget`` adapter (Ports & Adapters); this use case iterates a
registered ``list[StopTarget]`` ordered by ``phase`` instead of hardcoding the set,
so adding a target is a registration, not a code change here. Targets are grouped by
phase — phase 0 (task-cancel) completes before the phase-1 external fan-out so a late
continuation / step-result is dropped by the business-agent's per-task guard rather
than resurrecting the message. Every target is best-effort: it records failures in
its result and never aborts another. The per-target results are merged into
``StopConversationOutput`` (unchanged FE contract).

Authorization (TENANT-scoped) and observability stay here — they are pre/post
concerns of the fan-out, not targets themselves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.interfaces.stop_target import (
    StopContext,
    StopTarget,
    StopTargetResult,
)

# SA-892 (review, Felix): the sentinel tenant the controller/dispatch use when a
# bearer token carries no ``tenant_id``. Treated as "tenant unknown" for authz, so a
# token without a tenant never 403s a legitimate stop.
_UNSCOPED_TENANT = "default"


@dataclass
class StopConversationInput:
    conv_id: str = ""
    # SA-892 (L2 authz): identity of the authenticated caller, extracted from the
    # validated bearer token by the controller. Used to authorize the stop against
    # the conversation's owning tenant (TENANT_BLEED). Scoped to TENANT; user
    # id/email are carried for the audit log only.
    caller_tenant_id: str = ""
    caller_user_id: str = ""
    caller_email: str = ""


@dataclass
class StopConversationOutput:
    conv_id: str = ""
    agent_cancelled: bool = False
    # SA-892 (route-via-executor-tool): run ids for which a ``workflow_stop`` step
    # was successfully DISPATCHED to the executor (which performs the actual cancel).
    runs_cancelled: list[str] = field(default_factory=list)
    # SA-892 (task-scoped stop): ids of the current message's tasks marked CANCELLED.
    tasks_cancelled: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # SA-892 (L2 authz): True when REFUSED because the conversation belongs to a
    # different tenant than the caller (controller maps to HTTP 403; nothing cancelled).
    forbidden: bool = False


class StopConversationUseCase:
    """Fan a stop out to the registered stop targets for a conversation."""

    def __init__(
        self,
        logger: Any,
        monitor: Optional[Any] = None,
        orchestration_state_repo: Optional[Any] = None,
        stop_targets: Optional[list[StopTarget]] = None,
    ) -> None:
        # Structured logger (ILogger) — accepts kwargs; typed Any so ty does not
        # narrow to stdlib ``logging.Logger`` on the getLogger fallback.
        self.logger: Any = logger or logging.getLogger(__name__)
        self.monitor = monitor
        # Kept: authorization reads the conversation's owning tenant off shared state.
        self.orchestration_state_repo = orchestration_state_repo
        # NOTE: the fan-out collaborators (publisher / settings / uuid_builder /
        # conversation_run_index / agent_registry_client) live in the adapters now
        # (built by layer4 ``build_default_stop_targets``); the use case no longer
        # holds them — it only iterates the injected ``stop_targets``.
        # Ports & Adapters: the target list is injected (built at the composition
        # root — see layer4 ``build_default_stop_targets``). layer2 stays free of any
        # concrete adapter import. An empty list means only authorize + observability
        # run (no fan-out) — logged so a wiring gap is visible rather than silent.
        self._stop_targets: list[StopTarget] = list(stop_targets or [])
        if not self._stop_targets:
            self.logger.warning(
                "StopConversationUseCase built with NO stop targets; "
                "a Stop will authorize + observe but fan out to nothing"
            )

    async def execute(self, request: StopConversationInput) -> StopConversationOutput:
        conv_id = (request.conv_id or "").strip()
        output = StopConversationOutput(conv_id=conv_id)

        if not conv_id:
            output.errors.append("conv_id is required")
            self._emit_stop_observability(output)
            return output

        # SA-892 (L2 authz): authorize the caller against the conversation's owning
        # tenant BEFORE cancelling anything (a stop only requires a valid bearer
        # token, so without this any authenticated caller could stop any conv_id).
        if not self._authorize(conv_id, request, output):
            self._emit_stop_observability(output)
            return output

        await self._run_targets(conv_id, output)

        self._emit_stop_observability(output)
        return output

    async def _run_targets(
        self, conv_id: str, output: StopConversationOutput
    ) -> None:
        """Run each registered target in phase order, merging its result.

        Sorted by ``phase`` (stable — preserves registration order within a phase),
        so phase 0 (task-cancel) completes before the phase-1 external fan-out — the
        SA-892 ordering invariant. Sequential to preserve deterministic side-effect
        ordering; each target is best-effort and reports failures in its result.
        """
        ctx = StopContext(conv_id=conv_id)
        for target in sorted(self._stop_targets, key=lambda t: t.phase):
            result = await target.stop(ctx)
            self._merge(output, result)

    @staticmethod
    def _merge(output: StopConversationOutput, result: StopTargetResult) -> None:
        """Merge one target's result into the aggregate output (additive)."""
        if result.agent_cancelled:
            output.agent_cancelled = True
        output.tasks_cancelled.extend(result.tasks_cancelled)
        output.runs_cancelled.extend(result.runs_cancelled)
        output.errors.extend(result.errors)

    def _authorize(
        self,
        conv_id: str,
        request: StopConversationInput,
        output: StopConversationOutput,
    ) -> bool:
        """SA-892 (L2): TENANT-scoped authorization for the stop.

        Returns True if the stop may proceed, False if REFUSED (403). Refuse only
        when caller AND conversation each carry a REAL (non-sentinel) tenant that
        differ; an unresolvable conversation tenant proceeds best-effort (audit-logged).
        """
        caller_tenant = (request.caller_tenant_id or "").strip()
        conv_tenant = self._resolve_conversation_tenant(conv_id, output)

        caller_known = bool(caller_tenant) and caller_tenant != _UNSCOPED_TENANT
        conv_known = bool(conv_tenant) and conv_tenant != _UNSCOPED_TENANT

        if caller_known and conv_known and conv_tenant != caller_tenant:
            output.forbidden = True
            output.errors.append(
                "forbidden: conversation belongs to another tenant"
            )
            self.logger.warning(
                "orchestrate.stop.refused",
                conv_id=conv_id,
                caller_tenant_id=caller_tenant,
                caller_user_id=request.caller_user_id,
                caller_email=request.caller_email,
                conversation_tenant_id=conv_tenant,
            )
            return False

        if not conv_known:
            self.logger.info(
                "orchestrate.stop.tenant_unverified",
                conv_id=conv_id,
                caller_tenant_id=caller_tenant,
                caller_user_id=request.caller_user_id,
                caller_email=request.caller_email,
            )
        return True

    def _resolve_conversation_tenant(
        self, conv_id: str, output: StopConversationOutput
    ) -> str:
        """Resolve the conversation's owning tenant from agent shared state.

        NOT invented: an absent field, a missing conversation, a missing repo, or a
        read failure all resolve to ``""`` (unknown) so the caller proceeds
        best-effort rather than being wrongly refused. Read failures are recorded.
        """
        repo = self.orchestration_state_repo
        if repo is None:
            return ""

        get_conversation = getattr(repo, "get_conversation", None)
        if callable(get_conversation):
            try:
                conversation = get_conversation(conv_id)
            except Exception as exc:  # record, never abort / never refuse on error
                self.logger.warning(
                    "Conversation tenant lookup failed",
                    conv_id=conv_id,
                    error=str(exc),
                )
                output.errors.append(f"tenant lookup failed: {exc}")
                conversation = None
            if conversation is not None:
                tenant = self._tenant_of(conversation)
                if tenant:
                    return tenant
                metadata = getattr(
                    getattr(conversation, "shared_state", None), "metadata", None
                )
                tenant = self._tenant_of(metadata)
                if tenant:
                    return tenant

        try:
            metadata = repo.get_metadata(conv_id)
        except Exception as exc:  # record, never abort / never refuse on error
            self.logger.warning(
                "Metadata tenant lookup failed", conv_id=conv_id, error=str(exc)
            )
            output.errors.append(f"tenant lookup failed: {exc}")
            return ""
        return self._tenant_of(metadata)

    @staticmethod
    def _tenant_of(obj: Any) -> str:
        """Read a tenant identifier off *obj* if it carries one, else ``""``."""
        if obj is None:
            return ""
        for attr in ("tenant_id", "tenant"):
            value = getattr(obj, attr, None)
            if value:
                return str(value).strip()
        return ""

    def _emit_stop_observability(self, output: StopConversationOutput) -> None:
        """SA-892 (N11): emit stop metrics + one structured summary log.

        Guarded — observability must never break the stop path.
        """
        runs_cancelled = len(output.runs_cancelled)
        errors = len(output.errors)
        try:
            self.logger.info(
                "orchestrate.stop.summary",
                conv_id=output.conv_id,
                agent_cancelled=output.agent_cancelled,
                runs_cancelled=runs_cancelled,
                errors=errors,
            )
            monitor = self.monitor
            if monitor is not None:
                monitor.track("orchestrate.stop.requested", 1)
                if output.agent_cancelled:
                    monitor.track("orchestrate.stop.agent_cancelled", 1)
                if runs_cancelled:
                    monitor.track("orchestrate.stop.runs_cancelled", runs_cancelled)
                if errors:
                    monitor.track("orchestrate.stop.errors", errors)
        except Exception as exc:  # observability must never break the stop path
            self.logger.warning("Stop observability emit failed", error=str(exc))
