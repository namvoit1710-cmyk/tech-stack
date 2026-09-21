from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.layer1_domain.entities.workflow_design_result import WorkflowDesignResult
from app.layer3_adapters.controllers.restful.v1.design_stream_controller import (
    ConversationStore,
    router as design_stream_router,
)


class _FakeUseCase:
    """Records the history it was handed and emits a couple of progress events."""

    def __init__(self, result: WorkflowDesignResult) -> None:
        self._result = result
        self.seen_histories: list[list] = []
        self.seen_workflows: list = []

    async def design(self, message, history=None, progress=None, current_workflow=None):
        self.seen_histories.append(list(history or []))
        self.seen_workflows.append(current_workflow)
        if progress is not None:
            await progress.emit("planning", {"message": "Understanding your request…"})
            await progress.emit("created", {"workflow_id": "wf-1", "link": "/workflow/wf-1"})
        return self._result


def _build_client(use_case: _FakeUseCase) -> TestClient:
    app = FastAPI()
    app.state.design_use_case = use_case
    app.state.conversation_store = ConversationStore()
    app.include_router(design_stream_router)
    return TestClient(app)


def _parse_events(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_stream_emits_progress_then_done_with_conv_id():
    result = WorkflowDesignResult(
        status=WorkflowDesignResult.SUCCESS, content="done", workflow_id="wf-1",
        run_id="run-1", link="/workflow/wf-1",
    )
    client = _build_client(_FakeUseCase(result))

    events = _parse_events(client.get("/api/v1/design/stream?message=build+a+csv+flow").text)

    kinds = [e["event"] for e in events]
    assert kinds == ["planning", "created", "done"]
    done = events[-1]
    assert done["status"] == "success"
    assert done["workflow_id"] == "wf-1"
    assert done["conv_id"]  # a fresh id was minted and returned


def test_stream_threads_history_across_turns_on_same_conv_id():
    use_case = _FakeUseCase(
        WorkflowDesignResult(status=WorkflowDesignResult.NEEDS_CLARIFICATION, content="which file?")
    )
    client = _build_client(use_case)

    first = _parse_events(client.get("/api/v1/design/stream?message=make+a+flow").text)
    conv_id = first[-1]["conv_id"]

    client.get(f"/api/v1/design/stream?message=a+csv&conv_id={conv_id}")

    # First turn saw no prior history; the second turn saw the first turn's user + system turns.
    assert len(use_case.seen_histories) == 2
    assert [t.role for t in use_case.seen_histories[0]] == ["user"]
    assert [t.role for t in use_case.seen_histories[1]] == ["user", "system", "user"]
    assert use_case.seen_histories[1][0].message == "make a flow"


def test_stream_seeds_current_workflow_from_workflow_id_query():
    use_case = _FakeUseCase(
        WorkflowDesignResult(status=WorkflowDesignResult.SUCCESS, content="done", workflow_id="wf-42")
    )
    client = _build_client(use_case)

    client.get("/api/v1/design/stream?message=add+a+step&workflow_id=wf-42")

    # The incoming workflow_id is seeded so the agent knows to load + diff the existing workflow.
    assert use_case.seen_workflows[0] == {"workflow_id": "wf-42", "workflow": {}}


def test_stream_seeds_clone_source_from_source_workflow_id_query():
    use_case = _FakeUseCase(
        WorkflowDesignResult(status=WorkflowDesignResult.SUCCESS, content="done", workflow_id="wf-new")
    )
    client = _build_client(use_case)

    client.get("/api/v1/design/stream?message=make+a+copy+but+slack&source_workflow_id=wf-src")

    # SA-2131: the source id is seeded so the agent loads it as a template and clones (new id).
    assert use_case.seen_workflows[0] == {"source_workflow_id": "wf-src", "workflow": {}}


def test_stream_surfaces_use_case_failure_as_error_event():
    class _Boom(_FakeUseCase):
        async def design(self, message, history=None, progress=None, current_workflow=None):
            raise RuntimeError("planner down")

    client = _build_client(_Boom(WorkflowDesignResult()))
    events = _parse_events(client.get("/api/v1/design/stream?message=x").text)

    assert events[-1]["event"] == "error"
    assert "planner down" in events[-1]["message"]
