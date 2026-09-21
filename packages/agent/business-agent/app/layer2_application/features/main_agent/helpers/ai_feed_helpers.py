from dataclasses import dataclass, asdict
from typing import Optional

from agent_sdk.layer1_domain.entities.conversation_context import KnowledgeItem
from app.layer1_domain.entities.planning_context import (
    PlanningContextType,
)

from app.layer2_application.features.main_agent.dto.event import EventDTO
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.planner_resolver_interface import (
    IPlannerResolver,
)
from app.layer2_application.interfaces.workflow_client_interface import IWorkflowClient
from app.layer2_application.interfaces.push_gateway_notifier_interface import (
    IPushGatewayNotifier,
)

from app.layer1_domain.entities.rag_context import (
    IngestionRequest,
    IngestionUploadResponse,
    KnowledgeBase,
    CreateKnowledgeBaseRequest,
)

from app.layer2_application.interfaces.rag_client_interface import IRagClient
from app.layer2_application.interfaces.business_agent_shared_state_repository_interface import (
    IBusinessAgentSharedStateRepository,
)

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    GlobalContext,
    Metadata,
)
from app.layer2_application.interfaces.plan_generator_interface import IPlanGenerator
from app.layer1_domain.entities.planner import PlannerResponse
from app.layer1_domain.entities.workflow_event import (
    WorkflowEvent,
)
from app.layer1_domain.entities.ui_event import (
    UIPayload,
    UIPayloadType,
)
from app.layer2_application.interfaces.uuid_builder_interface import IUuidBuilder


from app.layer1_domain.entities.planning_context import (
    SummaryContext,
)


from dataclasses import asdict
from agent_sdk import Settings
from app.layer2_application.interfaces.file_client_interface import (
    FileData,
    IFileClient,
)


@dataclass
class AIFeedDeps:
    rag_client: IRagClient
    workflow_client: IWorkflowClient
    file_client: IFileClient
    logger: ILogger
    push_gateway: IPushGatewayNotifier
    planner_resolver: IPlannerResolver
    agent_shared_state_repository: IBusinessAgentSharedStateRepository
    uuid_builder: IUuidBuilder
    settings: Settings


async def execute_ai_feed_pipeline(
    *,
    event: EventDTO,
    conv_id: str,
    workflow_event: WorkflowEvent,
    deps: AIFeedDeps,
) -> None:

    logger = deps.logger

    # 1. Notify thinking
    await deps.push_gateway.send_thinking_event(
        conv_id=conv_id,
        thinking_message="I'm trying to summarize the execution...",
    )

    # 2. File ID
    file_id: Optional[str] = await _get_file_id(
        deps=deps,
        workflow_event=workflow_event,
    )
    if not file_id:
        logger.info("No file_id found, stopping pipeline")
        return

    # 3. File data
    file_data: Optional[FileData] = await _get_file_data(
        deps=deps,
        file_id=file_id,
    )
    if not file_data:
        logger.info("No file_data found, stopping pipeline")
        return

    # 4. Knowledge base
    kb: Optional[KnowledgeBase] = await _get_or_create_kb(
        deps=deps,
        conv_id=conv_id,
        event=event,
    )
    if not kb:
        logger.info("KB creation failed, stopping pipeline")
        return

    # 5. Ingestion
    ingestion: Optional[IngestionUploadResponse] = await _ingest_file(
        deps=deps,
        conv_id=conv_id,
        workflow_event=workflow_event,
        kb=kb,
        file_id=file_id,
    )
    if not ingestion:
        logger.info("Ingestion failed, stopping pipeline")
        return

    logger.info(
        "File ingested into KB",
        extra={
            "conv_id": conv_id,
            "kb_id": kb.knowledge_base_id,
            "file_id": file_id,
        },
    )

    # 6. Summary
    summary: str = await _generate_summary(
        deps=deps,
        file_data=file_data,
    )

    # 7. Context update
    await _update_context(
        deps=deps,
        conv_id=conv_id,
        workflow_event=workflow_event,
        kb=kb,
        ingestion=ingestion,
        file_id=file_id,
        summary=summary,
    )

    # 8. UI response
    await _send_ui_event(
        deps=deps,
        conv_id=conv_id,
        summary=summary,
    )


# -------------------------------------------------
# STEP 1: FILE ID
# -------------------------------------------------


async def _get_file_id(
    *,
    deps: AIFeedDeps,
    workflow_event: WorkflowEvent,
) -> Optional[str]:

    node_output = await deps.workflow_client.get_node_output_data(
        node_id=workflow_event.node_id,
        run_id=workflow_event.run_id,
    )

    if (
        not node_output
        or not node_output.data
        or not node_output.data.output
        or not node_output.data.output.root
    ):
        return None

    return node_output.data.output.root.file_id


async def _get_file_data(
    *,
    deps: AIFeedDeps,
    file_id: str,
) -> Optional[FileData]:

    return await deps.file_client.get_data_by_odata(
        file_id=file_id,
        top=10,
    )


async def _get_or_create_kb(
    *,
    deps: AIFeedDeps,
    conv_id: str,
    event: EventDTO,
) -> Optional[KnowledgeBase]:

    kb: KnowledgeBase | None = await deps.rag_client.get_knowledge_base_by_name(
        name=conv_id
    )
    if kb:
        return kb

    kb: KnowledgeBase | None = await deps.rag_client.create_knowledge_base(
        CreateKnowledgeBaseRequest(
            tenant_id=deps.settings.RAG_TENANT_ID,
            name=conv_id,
            description=f"Knowledge base for conversation {conv_id}",
        )
    )

    if kb is None:
        deps.logger.info(
            "Failed to create KB",
            event=asdict(event),
        )
    global_context: Optional[GlobalContext] = deps.agent_shared_state_repository.get_context_by_conv_id(conv_id)
    
    if global_context and kb:
        global_context.knowledge_base_id = kb.knowledge_base_id
        deps.agent_shared_state_repository.update_context(
            conv_id=conv_id,
            context=global_context,
        )
    return kb


async def _ingest_file(
    *,
    deps: AIFeedDeps,
    conv_id: str,
    workflow_event: WorkflowEvent,
    kb: KnowledgeBase,
    file_id: str,
) -> Optional[IngestionUploadResponse]:

    request = IngestionRequest(
        tenant_id=deps.settings.RAG_TENANT_ID,
        knowledge_base_ids=[kb.knowledge_base_id],
        file_id=file_id,
        filename=(
            f"conv_{conv_id}_"
            f"workflow_{workflow_event.workflow_id}_"
            f"file_{file_id}_"
            f"node_{workflow_event.node_id}_"
            f"run_{workflow_event.run_id}.csv"
        ),
    )

    return await deps.rag_client.ingestions(request)


async def _generate_summary(
    *,
    deps: AIFeedDeps,
    file_data: FileData,
) -> str:

    planner: IPlanGenerator = deps.planner_resolver.resolve(
        PlanningContextType.LLM_SUMMARY_CONTEXT_GENERATOR
    )

    plan: PlannerResponse = await planner.generate(
        SummaryContext(context=str(file_data))
    )

    return plan.answer or "No summary available"


async def _update_context(
    *,
    deps: AIFeedDeps,
    conv_id: str,
    workflow_event: WorkflowEvent,
    kb: KnowledgeBase,
    ingestion: IngestionUploadResponse,
    file_id: str,
    summary: str,
) -> None:
    global_context = deps.agent_shared_state_repository.get_context_by_conv_id(conv_id)

    if (
        not global_context
        or not global_context.conversation_context
    ):
        return

    metadata: Metadata = _get_metadata(deps=deps, conv_id=conv_id)

    if not metadata.current_task_id:
        return

    message_history = next(
        (
            msg
            for msg in global_context.conversation_context
            if msg.message_id == metadata.current_message_id
        ),
        None,
    )
    if not message_history:
        return

    task_history = next(
        (
            task
            for task in (message_history.tasks or [])
            if task.task_id == metadata.current_task_id
        ),
        None,
    )
    if not task_history:
        return

    global_context.knowledge_base_id = kb.knowledge_base_id

    node_summary = (
        f"Workflow {workflow_event.workflow_id}, "
        f"node {workflow_event.node_id} executed. "
        f"File {file_id} was ingested into the knowledge base. "
        f"RAG document ID: {ingestion.document.document_id}. "
        f"Summary: {summary}"
    )

    task_history.knowledge.append(
        KnowledgeItem(
            document_id=ingestion.document.document_id,
            summary=node_summary,
        )
    )

    deps.agent_shared_state_repository.update_context(
        conv_id=conv_id,
        context=global_context,
    )


async def _send_ui_event(
    *,
    deps: AIFeedDeps,
    conv_id: str,
    summary: str,
) -> None:

    metadata = _get_metadata(deps=deps, conv_id=conv_id)

    await deps.push_gateway.send_process_response_with_text_event(
        conv_id=conv_id,
        ui_payload=UIPayload(
            id=deps.uuid_builder.create_uuid7(),
            parent_id=metadata.current_task_id,
            type=UIPayloadType.TEXT,
            content=summary,
        ),
    )


def _get_metadata(deps: AIFeedDeps, conv_id: str) -> Metadata:
    metadata: Metadata | None = deps.agent_shared_state_repository.get_metadata(conv_id)

    if not metadata:
        raise ValueError(f"No metadata found for conversation {conv_id}")
    return metadata
