from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.layer1_domain.entities.designer_run_result import DesignerRunResult
from app.layer1_domain.entities.workflow_design_state import ConversationTurn
from app.layer2_application.interfaces.progress_sink_interface import IProgressSink
from app.layer2_application.interfaces.workflow_designer_interface import IWorkflowDesigner
from app.layer4_frameworks.designer.prompts import SYSTEM_PROMPT, existing_workflow_context

logger = logging.getLogger(__name__)

#: Never bound to the LLM during the autonomous design -> validate -> repair flow (AC2) —
#: run_workflow is invocable only on an explicit user re-test of an already-created workflow.
_NOT_AUTONOMOUS_TOOLS = frozenset({"run_workflow"})


class ToolLoopWorkflowDesigner(IWorkflowDesigner):
    """Designs a workflow via a bounded LLM think -> call-tool -> observe loop.

    The model discovers the live palette + config schemas through tools and builds a
    candidate workflow graph through repeated tool calls (no hardcoded node/config
    assembly, no deterministic fallback). Every step is streamed through ``progress``:
    ``thinking`` (the model's narration), ``tool_call`` and ``tool_result`` (the loop).
    The loop only designs — it hands the raw candidate back as a ``DesignerRunResult``;
    a ``ValidateRepairGate`` (Layer2) owns persisting, dry-running, repairing and the
    final test-run.
    """

    #: Hard ceiling on LLM turns so a flailing model can't spin forever.
    MAX_ITERATIONS = 10

    def __init__(
        self,
        llm_service: Any,
        tools: list[Any],
        system_prompt: str = SYSTEM_PROMPT,
        max_iterations: int | None = None,
    ) -> None:
        self._llm = llm_service
        self._tool_list = tools
        self._tools = {getattr(t, "name", ""): t for t in tools}
        self._system_prompt = system_prompt
        self._max = max_iterations or self.MAX_ITERATIONS

    async def run(
        self,
        message: str,
        history: list[ConversationTurn],
        progress: IProgressSink | None = None,
        current_workflow: dict | None = None,
    ) -> DesignerRunResult:
        design_tools = [t for t in self._tool_list if getattr(t, "name", "") not in _NOT_AUTONOMOUS_TOOLS]
        client = self._llm.get_chat_client().bind_tools(design_tools)
        system = self._system_prompt
        context = existing_workflow_context(current_workflow)
        if context:
            system = system + "\n\n" + context
        messages: list[BaseMessage] = [SystemMessage(content=system)]
        messages.extend(self._history_messages(history, message))

        await self._emit(progress, "planning", {"message": "Understanding your request…"})

        # The last "ready" candidate (create OR update) and the last authoring error (so a
        # rejected create/update surfaces its message). run_workflow is never bound above,
        # so it can never appear as a tool_call here (AC2). ``plan`` is the model's finalized
        # node plan (from plan_workflow), which the payload is self-reviewed against.
        ready: dict | None = None
        clarify: dict | None = None
        author_err: dict | None = None
        plan: dict | None = None
        final_text = ""
        # Catalog knowledge captured from the model's own list_node_types / get_node_schema
        # calls, so the payload self-review can enforce (without any extra fetch) that every
        # node is identified by a real catalog id (from which the control-plane client resolves
        # node_type + worker_type + icon/colour) and fills its required config. ``cat_ids`` =
        # catalog ids; ``cat_schemas`` maps id/node_type -> schema dict.
        cat_ids: set[str] = set()
        cat_schemas: dict[str, dict] = {}

        for _ in range(self._max):
            response, streamed = await self._generate(client, messages, progress)
            messages.append(response)
            final_text = self._text(getattr(response, "content", "")).strip()
            if final_text:
                # If we streamed tokens live, just close the line; otherwise emit the
                # whole narration once. Trailing newline keeps per-step lines separate
                # when the FE accumulates thinking deltas.
                await self._emit(progress, "thinking",
                                 {"delta": "\n" if streamed else final_text + "\n"})

            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                break

            for call in tool_calls:
                name = call.get("name", "")
                args = call.get("args") or {}
                await self._emit(progress, "tool_call",
                                 {"tool": name, "args_summary": self._args_summary(name, args)})
                result = await self._execute(name, args)
                await self._emit(progress, "tool_result",
                                 {"tool": name, "summary": self._result_summary(name, result)})
                messages.append(ToolMessage(
                    content=json.dumps(result, default=str),
                    tool_call_id=call.get("id") or name,
                    name=name,
                ))
                if name == "list_node_types":
                    for nt in (result.get("node_types") or []):
                        if isinstance(nt, dict) and nt.get("id"):
                            cat_ids.add(str(nt["id"]))
                elif name == "get_node_schema":
                    for s in (result.get("schemas") or []):
                        if not isinstance(s, dict):
                            continue
                        if s.get("id"):
                            cat_schemas[str(s["id"])] = s
                        if s.get("node_type"):
                            cat_schemas[str(s["node_type"]).upper()] = s
                elif name == "plan_workflow":
                    plan = result
                    await self._emit(progress, "plan",
                                     {"nodes": result.get("nodes") or [],
                                      "rationale": result.get("rationale") or ""})
                    if result.get("critique"):
                        await self._emit(progress, "critique", {"critique": result["critique"]})
                    logger.info("designer plan: %d node(s); critique=%r",
                                len(result.get("nodes") or []), result.get("critique") or "")
                elif name in ("create_workflow", "update_workflow", "clone_workflow"):
                    if result.get("status") == "ready":
                        # Self-review the built payload before handing it to the gate: it must
                        # match the finalized plan, use only real catalog node types, and fill
                        # every node's required config. On any issue, feed it back so the model
                        # reconciles (bounded by MAX_ITERATIONS) instead of shipping a bad graph.
                        # update/clone hand over a partial diff (only the touched nodes), so the
                        # whole-graph plan-match is skipped for them — it runs on the merged graph.
                        known = {"ids": cat_ids, "schemas": cat_schemas}
                        issues = self._review_payload(
                            plan, result.get("definition") or {}, known,
                            is_update=(name in ("update_workflow", "clone_workflow")))
                        await self._emit(progress, "review", {"ok": not issues, "issues": issues})
                        if issues:
                            logger.info("payload self-review found issues: %s", issues)
                            messages.append(HumanMessage(content=(
                                "Self-review found problems with your payload — "
                                + "; ".join(issues)
                                + f". Fix these and call {name} again with the corrected graph.")))
                            ready = None
                        else:
                            ready = result
                            author_err = None
                    elif result.get("status") in ("invalid", "error"):
                        author_err = result
                elif name == "ask_for_clarify":
                    clarify = result

            if clarify is not None:
                break

        return self._build_outcome(ready, clarify, final_text, author_err)

    # --- helpers ---

    async def _generate(
        self, client: Any, messages: list[BaseMessage], progress: IProgressSink | None
    ) -> tuple[BaseMessage, bool]:
        """One LLM turn. When a listener is attached, stream tokens live as ``thinking``
        deltas (real-time generation) and assemble the full message (incl. tool calls) from
        the chunks; fall back to a single ``ainvoke`` if streaming isn't available.

        Returns ``(message, streamed)`` where ``streamed`` is True when tokens were emitted.
        """
        if progress is None:
            return await client.ainvoke(messages), False
        try:
            full = None
            emitted = False
            async for chunk in client.astream(messages):
                full = chunk if full is None else full + chunk
                delta = self._text(getattr(chunk, "content", ""))
                if delta:
                    await progress.emit("thinking", {"delta": delta})
                    emitted = True
            if full is not None:
                return full, emitted
        except Exception as exc:  # streaming unsupported/failed -> non-streaming fallback
            logger.info("astream failed (%s); falling back to ainvoke", exc)
        return await client.ainvoke(messages), False

    async def _execute(self, name: str, args: dict) -> dict:
        tool = self._tools.get(name)
        if tool is None:
            return {"status": "error", "message": f"Unknown tool '{name}'."}
        try:
            result = await tool.ainvoke(args or {})
        except Exception as exc:  # a tool failure is fed back to the model, not fatal
            logger.info("tool %s failed: %s", name, exc)
            return {"status": "error", "message": f"{name} failed: {exc}"}
        return result if isinstance(result, dict) else {"result": result}

    @staticmethod
    def _history_messages(history: list[ConversationTurn], message: str) -> list[BaseMessage]:
        turns = list(history or [])
        if not turns:
            return [HumanMessage(content=message)]
        out: list[BaseMessage] = []
        for turn in turns:
            if turn.role == "user":
                out.append(HumanMessage(content=turn.message))
            else:
                out.append(AIMessage(content=turn.message))
        return out

    @staticmethod
    def _text(content: Any) -> str:
        """Flatten a message content (str or list of content blocks) to text."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            return "".join(parts)
        return ""

    @staticmethod
    def _args_summary(name: str, args: dict) -> str:
        if name == "get_node_schema":
            ids = args.get("node_ids") or []
            return "reading config for " + (", ".join(str(i) for i in ids) or "nodes")
        if name == "plan_workflow":
            return f"planning {len(args.get('nodes') or [])} node(s)"
        if name in ("validate_workflow", "create_workflow", "update_workflow", "clone_workflow"):
            verb = {"validate_workflow": "validating", "create_workflow": "creating",
                    "update_workflow": "updating", "clone_workflow": "cloning"}[name]
            return f"{verb} '{args.get('name') or 'workflow'}'"
        if name == "run_workflow":
            return "running the workflow"
        if name == "ask_for_clarify":
            return str(args.get("question") or "asking for details")
        if name == "list_node_types":
            return "listing available node types"
        if name == "find_workflows":
            return f"finding workflow '{args.get('query') or ''}'"
        if name == "get_workflow":
            return f"loading workflow {args.get('workflow_id') or ''}".strip()
        if name == "diagnose_failure":
            ref = args.get("run_id") or args.get("workflow_name") or args.get("workflow_id") or ""
            return f"diagnosing failed run {ref}".strip()
        return name

    @staticmethod
    def _result_summary(name: str, result: dict) -> str:
        status = result.get("status")
        if name == "plan_workflow":
            return f"{len(result.get('nodes') or [])} node(s) planned"
        if name == "list_node_types":
            return f"{len(result.get('node_types', []))} node types available"
        if name == "get_node_schema":
            got = len(result.get("schemas", []))
            missing = result.get("missing") or []
            return f"got {got} schema(s)" + (f", missing {', '.join(missing)}" if missing else "")
        if name == "validate_workflow":
            return "valid" if result.get("valid") else f"invalid: {result.get('error')}"
        if name in ("create_workflow", "update_workflow", "clone_workflow"):
            if status == "ready":
                return "design ready"
            return f"{status}: {result.get('message') or result.get('error') or ''}".strip()
        if name == "run_workflow":
            if status == "run":
                return f"test run: {result.get('run_error') or result.get('run_status') or 'ok'}"
            return f"{status}: {result.get('message') or ''}".strip()
        if name == "ask_for_clarify":
            return str(result.get("question") or "")
        if name == "find_workflows":
            matches = result.get("matches") or []
            return f"{len(matches)} match(es)" if status == "ok" else f"{status}: {result.get('message') or ''}".strip()
        if name == "get_workflow":
            return "loaded" if status == "loaded" else f"{status}: {result.get('message') or ''}".strip()
        if name == "diagnose_failure":
            if status == "diagnosed":
                nodes = result.get("failed_node_ids") or []
                return f"{result.get('category') or 'failure'} in node(s) {', '.join(nodes) or '?'}"
            return f"{status}: {result.get('message') or ''}".strip()
        return str(status or "done")

    _MD_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://|www\.)[^)]*\)")
    _BARE_URL = re.compile(r"(?:https?://|www\.)\S+")

    @classmethod
    def _strip_links(cls, text: str) -> str:
        """Remove any URL / markdown link from the model's reply. The real link is delivered
        structurally (workflow_id/link) and rendered by the UI, so a model-written link is
        always either redundant or a hallucination (e.g. a fabricated example.com id)."""
        t = cls._MD_LINK.sub(r"\1", text or "")   # [label](url) -> label
        t = cls._BARE_URL.sub("", t)              # bare url -> gone
        return re.sub(r"[ \t]{2,}", " ", t).strip()

    @staticmethod
    def _is_blank(value: Any) -> bool:
        """A config value counts as unfilled when it's None, an empty/whitespace string,
        or an empty collection. A binding like ``{{$input}}`` or ``0``/``False`` is filled."""
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, dict)):
            return len(value) == 0
        return False

    @classmethod
    def _review_payload(
        cls,
        plan: dict | None,
        definition: dict,
        known: dict | None = None,
        is_update: bool = False,
    ) -> list[str]:
        """Self-review the built payload before handing it to the gate. Each check is a no-op
        when its input is absent (so it never false-alarms):

        1. **Plan match** — node ids equal the finalized plan's (no missing/extra node).
        2. **Catalog identity** — every node's ``type`` (or its ``config.worker_type``) is an
           exact catalog id the model listed. The control-plane client resolves node_type +
           worker_type + icon/colour from that id, so a node that is NOT a real catalog id can't
           be resolved and would persist unbound (worker_type: null) — it's rejected here.
        3. **Required config** — every node fills the REQUIRED config fields of its schema.

        ``known`` carries the catalog knowledge the model already fetched: ``{"ids", "schemas"}``.

        Returns human-readable discrepancies, or [] when the payload holds.
        """
        nodes = [n for n in (definition.get("nodes") or []) if isinstance(n, dict)]
        known = known or {}
        ids = known.get("ids") or set()
        schemas = known.get("schemas") or {}
        issues: list[str] = []

        # 1. plan match (by id) — skipped for an update, whose payload is intentionally a
        # partial diff (only the added/changed nodes), not the whole planned graph.
        planned = plan.get("nodes") if isinstance(plan, dict) else None
        planned_ids = {str(n.get("id")) for n in (planned or [])
                       if isinstance(n, dict) and n.get("id") is not None}
        if planned_ids and not is_update:
            actual_ids = {str(n.get("id")) for n in nodes if n.get("id") is not None}
            missing = sorted(planned_ids - actual_ids)
            extra = sorted(actual_ids - planned_ids)
            if missing:
                issues.append(f"missing planned node(s): {missing}")
            if extra:
                issues.append(f"unplanned extra node(s): {extra}")

        for n in nodes:
            nid = n.get("id")
            ntype = str(n.get("type") or "").strip()
            cfg = n.get("config") if isinstance(n.get("config"), dict) else {}
            wtype = str(cfg.get("worker_type") or "").strip()

            # 2. catalog identity — the node must be a real catalog id
            if ids and ntype not in ids and wtype not in ids:
                issues.append(
                    f"node {nid!r} has type {ntype!r} which is not a catalog id — set its "
                    "\"type\" to the exact id from list_node_types (e.g. "
                    "\"worker:http_request\"), otherwise it persists unbound (worker_type: null)")

            # 3. required config filled (per this node's schema)
            if schemas:
                ref = wtype or ntype
                schema = schemas.get(ref) or schemas.get(ref.upper())
                if schema:
                    required = [str(f.get("key")) for f in (schema.get("config_fields") or [])
                                if isinstance(f, dict) and f.get("required") and f.get("key")]
                    missing_cfg = [k for k in required if cls._is_blank(cfg.get(k))]
                    if missing_cfg:
                        issues.append(
                            f"node {nid!r} is missing required config: {missing_cfg}")
        return issues

    def _build_outcome(
        self,
        ready: dict | None,
        clarify: dict | None,
        final_text: str,
        author_err: dict | None = None,
    ) -> DesignerRunResult:
        if clarify is not None:
            return DesignerRunResult(
                status=DesignerRunResult.NEEDS_CLARIFICATION,
                clarify_question=clarify.get("question") or "Could you give me a bit more detail?",
            )
        # Strip any link/URL the model wrote — informational only; the gate builds the real result.
        text = self._strip_links(final_text)
        if ready is not None:
            return DesignerRunResult(
                status=DesignerRunResult.CANDIDATE,
                definition=ready.get("definition") or {},
                workflow_id=ready.get("workflow_id", ""),
                source_workflow_id=ready.get("source_workflow_id", ""),
                remove_node_ids=ready.get("remove_node_ids") or [],
                content=text,
            )
        # A create/update was attempted but rejected.
        if author_err is not None:
            return DesignerRunResult(
                status=DesignerRunResult.ERROR,
                error_message=author_err.get("message") or author_err.get("error")
                              or "The workflow could not be designed.",
            )
        return DesignerRunResult(
            status=DesignerRunResult.ERROR,
            error_message=text or "I couldn't design a workflow for that request. Please add more detail.",
        )

    @staticmethod
    async def _emit(progress: IProgressSink | None, event: str, data: dict) -> None:
        if progress is not None:
            await progress.emit(event, data)
