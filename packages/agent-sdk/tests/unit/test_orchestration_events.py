def test_event_envelope_uses_canonical_queue_fields():
    from agent_sdk.layer1_domain.entities.event_envelope import EventEnvelope

    event = EventEnvelope(
        type="agent.plan.created",
        conversation_id="conv-1",
        payload={"ok": True},
    )

    assert event.type == "agent.plan.created"
    assert event.conversation_id == "conv-1"
    assert event.payload == {"ok": True}


def test_event_envelope_supports_business_context_correlation_fields():
    from agent_sdk.layer1_domain.entities.event_envelope import EventEnvelope

    event = EventEnvelope(
        type="executor.request.agent",
        message_id="msg-1",
        correlation_id="corr-1",
        conversation_id="conv-1",
        payload={"ok": True},
    )

    assert event.message_id == "msg-1"
    assert event.correlation_id == "corr-1"


def test_task_and_step_definitions_model_queue_contracts():
    from agent_sdk.layer1_domain.entities.orchestration_events import (
        StepDefinition,
        TaskDefinition,
    )

    step = StepDefinition(step_id="step-1", title="Review")
    task = TaskDefinition(task_id="task-1", name="Approve plan", steps=[step])

    assert task.steps[0].step_id == "step-1"
    assert task.steps[0].title == "Review"


def test_orchestration_helpers_build_canonical_events():
    from agent_sdk.layer1_domain.entities.orchestration_events import (
        AGENT_PLAN_CREATED,
        AGENT_PLAN_ERROR,
        AGENT_PLAN_EXECUTED,
        AGENT_PLAN_EXECUTING,
        AGENT_PLAN_SUCCESS,
        AGENT_REQUEST_AGENT,
        CONVERSATION_PLAN_CREATED,
        OrchestrationEventType,
        StepDefinition,
        TaskDefinition,
        create_agent_plan_created_event,
        create_agent_plan_error_event,
        create_agent_plan_executed_event,
        create_agent_plan_executing_event,
        create_agent_plan_success_event,
        create_agent_request_event,
        create_conversation_plan_created_event,
    )

    task = TaskDefinition(task_id="task-1", name="Approve")
    step = StepDefinition(step_id="step-1", title="Execute")

    assert (
        CONVERSATION_PLAN_CREATED
        == OrchestrationEventType.CONVERSATION_PLAN_CREATED.value
    )
    assert AGENT_PLAN_CREATED == OrchestrationEventType.AGENT_PLAN_CREATED.value
    assert AGENT_PLAN_EXECUTING == OrchestrationEventType.AGENT_PLAN_EXECUTING.value
    assert AGENT_PLAN_EXECUTED == OrchestrationEventType.AGENT_PLAN_EXECUTED.value
    assert AGENT_REQUEST_AGENT == OrchestrationEventType.AGENT_REQUEST_AGENT.value
    assert AGENT_PLAN_SUCCESS == OrchestrationEventType.AGENT_PLAN_SUCCESS.value
    assert AGENT_PLAN_ERROR == OrchestrationEventType.AGENT_PLAN_ERROR.value

    assert (
        create_conversation_plan_created_event("conv-1", [task]).type
        == CONVERSATION_PLAN_CREATED
    )
    assert create_agent_plan_created_event("conv-1", task).type == AGENT_PLAN_CREATED
    assert (
        create_agent_plan_executing_event("conv-1", step).type == AGENT_PLAN_EXECUTING
    )
    assert create_agent_plan_executed_event("conv-1", step).type == AGENT_PLAN_EXECUTED
    assert (
        create_agent_request_event("conv-1", {"agent_id": "a1"}).type
        == AGENT_REQUEST_AGENT
    )
    assert (
        create_agent_plan_success_event("conv-1", {"status": "ok"}).type
        == AGENT_PLAN_SUCCESS
    )
    assert (
        create_agent_plan_error_event("conv-1", {"error": "boom"}).type
        == AGENT_PLAN_ERROR
    )


def test_executor_step_helpers_build_business_context_events():
    from agent_sdk.layer1_domain.entities.executor_step_contract import (
        ExecutorStep,
        ExecutorStepEventPayload,
    )
    from agent_sdk.layer1_domain.entities.orchestration_events import (
        AGENT_STEP_STATUS,
        EXECUTOR_REQUEST_AGENT,
        EXECUTOR_REQUEST_STEP_BATCH,
        EXECUTOR_STEP_STATUS,
        OrchestrationEventType,
        create_agent_step_status_event,
        create_executor_request_agent_event,
        create_executor_request_step_batch_event,
        create_executor_step_status_event,
    )

    step = ExecutorStep(
        id="step-1",
        execute_by="agent.file",
        next_step_id=None,
        goal="Normalize uploaded files",
        message="Normalize the uploaded files for downstream use",
        uploaded_file_ids=["file-1"],
        input_schema=["step-0.file_id"],
        output_schema=["step-1.file_id"],
        status="processing",
    )
    batch_payload = ExecutorStepEventPayload(
        from_agent="agent.business",
        to_agent="executor.runtime",
        payload_type="step_batch",
        batch_id="batch-1",
        steps=[step],
    )
    request_payload = ExecutorStepEventPayload(
        from_agent="executor.runtime",
        to_agent="agent.file",
        payload_type="step",
        step=step,
    )
    status_payload = ExecutorStepEventPayload(
        from_agent="agent.file",
        to_agent="executor.runtime",
        payload_type="step",
        step=step,
    )

    assert (
        EXECUTOR_REQUEST_STEP_BATCH
        == OrchestrationEventType.EXECUTOR_REQUEST_STEP_BATCH.value
    )
    assert EXECUTOR_REQUEST_AGENT == OrchestrationEventType.EXECUTOR_REQUEST_AGENT.value
    assert AGENT_STEP_STATUS == OrchestrationEventType.AGENT_STEP_STATUS.value
    assert EXECUTOR_STEP_STATUS == OrchestrationEventType.EXECUTOR_STEP_STATUS.value

    batch_event = create_executor_request_step_batch_event(
        "conv-1",
        batch_payload,
        message_id="msg-batch",
        correlation_id="corr-batch",
    )
    request_event = create_executor_request_agent_event(
        "conv-1",
        request_payload,
        message_id="msg-request",
        correlation_id="corr-request",
    )
    agent_status_event = create_agent_step_status_event(
        "conv-1",
        status_payload,
        message_id="msg-agent-status",
        correlation_id="corr-request",
    )
    executor_status_event = create_executor_step_status_event(
        "conv-1",
        status_payload,
        message_id="msg-executor-status",
        correlation_id="corr-request",
    )

    assert batch_event.type == EXECUTOR_REQUEST_STEP_BATCH
    assert batch_event.message_id == "msg-batch"
    assert batch_event.correlation_id == "corr-batch"
    assert batch_event.payload == {
        "from": "agent.business",
        "to": "executor.runtime",
        "type": "step_batch",
        "batch_id": "batch-1",
        "steps": [
            {
                "id": "step-1",
                "execute_by": "agent.file",
                "next_step_id": None,
                "goal": "Normalize uploaded files",
                "message": "Normalize the uploaded files for downstream use",
                "uploaded_file_ids": ["file-1"],
                "input_schema": ["step-0.file_id"],
                "output_schema": ["step-1.file_id"],
                "status": "processing",
            }
        ],
    }
    assert request_event.type == EXECUTOR_REQUEST_AGENT
    assert request_event.message_id == "msg-request"
    assert request_event.correlation_id == "corr-request"
    assert request_event.payload["step"]["id"] == "step-1"
    assert request_event.payload["from"] == "executor.runtime"
    assert request_event.payload["to"] == "agent.file"
    assert agent_status_event.type == AGENT_STEP_STATUS
    assert agent_status_event.payload["step"]["status"] == "processing"
    assert executor_status_event.type == EXECUTOR_STEP_STATUS
    assert executor_status_event.payload["step"]["execute_by"] == "agent.file"


def test_top_level_sdk_exports_executor_step_helpers():
    from agent_sdk import (
        AGENT_STEP_STATUS,
        EXECUTOR_REQUEST_AGENT,
        EXECUTOR_REQUEST_STEP_BATCH,
        EXECUTOR_STEP_STATUS,
        ExecutorStep,
        ExecutorStepEventPayload,
        create_agent_step_status_event,
        create_executor_request_agent_event,
        create_executor_request_step_batch_event,
        create_executor_step_status_event,
    )

    assert EXECUTOR_REQUEST_STEP_BATCH == "executor.request.step_batch"
    assert EXECUTOR_REQUEST_AGENT == "executor.request.agent"
    assert AGENT_STEP_STATUS == "agent.step.status"
    assert EXECUTOR_STEP_STATUS == "executor.step.status"
    assert ExecutorStep is not None
    assert ExecutorStepEventPayload is not None
    assert create_executor_request_step_batch_event is not None
    assert create_executor_request_agent_event is not None
    assert create_agent_step_status_event is not None
    assert create_executor_step_status_event is not None
