from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.layer1_domain.entities.workflow_design_state import ConversationTurn

router = APIRouter()


class _QueueProgressSink:
    """Progress sink backed by an asyncio.Queue the SSE generator drains."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

    async def emit(self, event: str, data: dict) -> None:
        await self.queue.put({"event": event, **data})


class ConversationStore:
    """In-memory conversation history keyed by conv_id.

    The streaming endpoint bypasses the SDK graph (which threads history via the
    checkpointer), so it keeps its own history here to support multi-turn clarify
    exactly like ``POST /api/v1/execute``. Per-process, matching this endpoint's
    existing in-process design; not shared across replicas.
    """

    def __init__(self) -> None:
        self._by_conv: dict[str, list[ConversationTurn]] = {}
        self._workflow_by_conv: dict[str, dict] = {}

    def history(self, conv_id: str) -> list[ConversationTurn]:
        """A copy of the prior turns for this conversation (never the live list)."""
        return list(self._by_conv.get(conv_id, []))

    def save(self, conv_id: str, history: list[ConversationTurn]) -> None:
        self._by_conv[conv_id] = list(history)

    def workflow(self, conv_id: str) -> dict | None:
        """The workflow (id + document) this conversation is building, if any."""
        return self._workflow_by_conv.get(conv_id)

    def save_workflow(self, conv_id: str, workflow_id: str, document: dict) -> None:
        if workflow_id:
            self._workflow_by_conv[conv_id] = {"workflow_id": workflow_id, "workflow": document or {}}


def _get_store(request: Request) -> ConversationStore:
    store = getattr(request.app.state, "conversation_store", None)
    if store is None:
        store = ConversationStore()
        request.app.state.conversation_store = store
    return store


@router.get("/api/v1/design/stream")
async def design_stream(
    request: Request,
    message: str = Query(..., min_length=1),
    conv_id: str | None = Query(None),
    workflow_id: str | None = Query(None),
    source_workflow_id: str | None = Query(None),
):
    """Run the design flow and stream each step as Server-Sent Events.

    Pass ``conv_id`` to continue a conversation (e.g. answer a clarifying
    question); omit it to start a new one — the id is returned on the ``done``
    event so the client can thread follow-ups. Pass ``workflow_id`` to edit an
    EXISTING workflow (SA-2157): the agent loads it (via get_workflow) and applies
    the change as a diff instead of building a new workflow. Pass
    ``source_workflow_id`` to CLONE (SA-2131): the agent loads that workflow as a
    template and saves the change as a NEW workflow, leaving the source unchanged.
    """
    use_case = request.app.state.design_use_case
    store = _get_store(request)
    conversation_id = conv_id or uuid.uuid4().hex
    sink = _QueueProgressSink()

    async def _run() -> None:
        try:
            # Mirror the graph node: prior turns + this user message -> design -> record the reply.
            history = store.history(conversation_id)
            history.append(ConversationTurn(role="user", message=message))
            current = store.workflow(conversation_id)
            # Seed the intent when this conversation isn't already tracking a workflow:
            #  - source_workflow_id -> CLONE the source into a new workflow (SA-2131);
            #  - workflow_id -> EDIT that existing workflow in place (SA-2157).
            # In both cases the agent loads it via get_workflow and applies the change as a diff.
            if not (current and current.get("workflow_id")):
                if source_workflow_id:
                    current = {"source_workflow_id": source_workflow_id, "workflow": {}}
                elif workflow_id:
                    current = {"workflow_id": workflow_id, "workflow": {}}
            result = await use_case.design(message, history, progress=sink, current_workflow=current)
            history.append(ConversationTurn(role="system", message=result.content))
            store.save(conversation_id, history)
            # Remember the workflow this conversation is building, so the next turn can
            # UPDATE it instead of creating a new one.
            if result.workflow_id:
                store.save_workflow(conversation_id, result.workflow_id, result.workflow)
            await sink.queue.put({
                "event": "done",
                "conv_id": conversation_id,
                "status": result.status,
                "content": result.content,
                "workflow_id": result.workflow_id,
                "run_id": result.run_id,
                "link": result.link,
            })
        except Exception as exc:  # surface any failure into the stream
            await sink.queue.put({"event": "error", "message": str(exc)})
        finally:
            await sink.queue.put(None)

    async def _events():
        task = asyncio.create_task(_run())
        try:
            while True:
                item = await sink.queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
