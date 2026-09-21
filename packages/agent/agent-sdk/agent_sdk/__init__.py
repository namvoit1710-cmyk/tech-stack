from __future__ import annotations

import importlib
import sys
from typing import Any

__all__ = [
    "run_agent",
    "build_app_container",
    "scan_and_load_features",
    "register_message_reaction_handlers",
    "Settings",
    "settings",
    "HanaCredentials",
    "get_hana_credentials",
    "AgentInfo",
    "AgentRequest",
    "AgentResponse",
    "AgentRegistration",
    "ToolRegistration",
    "ToolRegistrar",
    "AgentRuntimeConfig",
    "ExecutionPolicy",
    "QueueMetadata",
    "AgentBaseState",
    "SharedStateDocument",
    "SharedStateLockInfo",
    "SharedStateRecord",
    "InboxRecord",
    "OutboxRecord",
    "TenantContext",
    "ConversationMetadata",
    "EventEnvelope",
    "UiEventType",
    "UiPayloadType",
    "WfInfo",
    "ProgressingCollapsePayload",
    "ToolFormPayload",
    "TextPayload",
    "ButtonAction",
    "ButtonGroupPayload",
    "OpenWorkspacePayload",
    "SummaryPayload",
    "ChatThinkingEvent",
    "ChatResponseEvent",
    "ChatDisabledEvent",
    "ChatEnabledEvent",
    "OrchestrationEventType",
    "TaskDefinition",
    "StepDefinition",
    "ExecutorStep",
    "ExecutorStepEventPayload",
    "CONVERSATION_PLAN_CREATED",
    "AGENT_PLAN_CREATED",
    "AGENT_PLAN_EXECUTING",
    "AGENT_PLAN_EXECUTED",
    "AGENT_REQUEST_AGENT",
    "AGENT_PLAN_SUCCESS",
    "AGENT_PLAN_ERROR",
    "EXECUTOR_REQUEST_STEP_BATCH",
    "EXECUTOR_REQUEST_AGENT",
    "AGENT_STEP_STATUS",
    "EXECUTOR_STEP_STATUS",
    "create_conversation_plan_created_event",
    "create_agent_plan_created_event",
    "create_agent_plan_executing_event",
    "create_agent_plan_executed_event",
    "create_agent_request_event",
    "create_agent_plan_success_event",
    "create_agent_plan_error_event",
    "create_executor_request_step_batch_event",
    "create_executor_request_agent_event",
    "create_agent_step_status_event",
    "create_executor_step_status_event",
    "HitlInterruptPayload",
    "InterruptType",
    "HitlResumeCommand",
    "WorkflowEvent",
    "WorkflowStartedEvent",
    "UiRenderRequestEvent",
    "WorkflowGuidelineRenderRequestEvent",
    "NodeStartedEvent",
    "InputValidatingEvent",
    "NodeWaitingUserEvent",
    "InputUpdatedEvent",
    "NodeCompletedEvent",
    "WorkflowCompletedEvent",
    "WorkflowFailedEvent",
    "UnsupportedFeatureEvent",
    "NodeUpdatedEvent",
    "NodeDataInitializedEvent",
    "AgentSelectedEvent",
    "ToolSelectedEvent",
    "EVENT_WORKFLOW_STARTED",
    "EVENT_UI_RENDER_REQUEST",
    "EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST",
    "EVENT_NODE_STARTED",
    "EVENT_INPUT_VALIDATING",
    "EVENT_NODE_WAITING_USER",
    "EVENT_INPUT_UPDATED",
    "EVENT_NODE_COMPLETED",
    "EVENT_WORKFLOW_COMPLETED",
    "EVENT_WORKFLOW_FAILED",
    "EVENT_UNSUPPORTED_FEATURE",
    "EVENT_NODE_UPDATED",
    "EVENT_NODE_DATA_INITIALIZED",
    "EVENT_AGENT_SELECTED",
    "EVENT_TOOL_SELECTED",
    "AgentSDKError",
    "GraphCompilationError",
    "NodeExecutionError",
    "RegistrationError",
    "DependencyError",
    "BusinessRuleException",
    "WorkflowKnownIssueException",
    "DelegationTimeoutException",
    "QueueDeliveryException",
    "AgentStatus",
    "TransportState",
    "TransportSource",
    "MessageTypeSet",
    "NodeType",
    "AgentCallRequest",
    "AgentCallResult",
    "ILogger",
    "IMonitor",
    "IAgentRegistry",
    "IToolRegistry",
    "IAgentPipeline",
    "IChatCompletionService",
    "ILLMService",
    "IInputGuard",
    "IOutputGuard",
    "IMessagePublisher",
    "IMessageConsumer",
    "IWorkflowEventEmitter",
    "IAgentEndpointResolver",
    "IAgentDelegator",
    "IMCPClientService",
    "ISharedStateRepository",
    "IInboxRepository",
    "IOutboxRepository",
    "IMessageDelivery",
    "MCPClientService",
    "ExecuteAgentInput",
    "ExecuteAgentOutput",
    "ResumeAgentInput",
    "ResumeAgentOutput",
    "ResumeAgentUseCase",
    "AgentGraphBuilder",
    "ToolAgentBuilder",
    "FlowGraphBuilder",
    "StateGraph",
    "END",
    "ToolNode",
    "tools_condition",
    "interrupt",
    "FlowConfig",
    "StepConfig",
    "NodeTypeRegistry",
    "run_consumer_agent",
    "input_guard_node",
    "output_guard_node",
    "error_handler_node",
    "format_response_node",
    "ToolAgentState",
    "LangChainLLMService",
    "OpenAIService",
    "make_llm_service",
    "make_openai_service",
    "tool",
    "RemoteAgentTool",
    "HttpAgentRegistry",
    "ToolRegistrar",
    "RegistryEndpointResolver",
    "HanaCheckpointSaver",
    "HanaSharedStateRepository",
    "HanaInboxRepository",
    "HanaOutboxRepository",
    "HanaConnectionManager",
    "create_checkpointer",
    "ensure_definition",
    "DependencyResolver",
    "ensure_dependency_resolver",
    "StateResolver",
    "ensure_state_resolver",
    "extract_state_snapshot",
    "merge_state_snapshot",
    "create_agent_app",
    "HumanMessage",
    "SystemMessage",
    "AIMessage",
    "WorkflowEventEmitter",
    "OutboxPublisher",
    "DurableMessageDeduplicator",
    "serialize_event",
    "workflow_event_scope",
    "emit_workflow_event",
    "AgentCallCoordinator",
    "ContextBudgetManager",
    "ContextBudgetConfig",
    "CompactionStrategy",
    "PayloadType",
    "AgentCapability",
    "AgentDiscoveryService",
    "default_tool_factory",
    "AsyncAgentDelegator",
    "MessageReactionRouter",
    "MessageHandlingResult",
    "LLMUsageRecord",
    "LLMCostCalculator",
    "LoggingLLMUsageRecorder",
    "UsageTrackingChatModel",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "run_agent": ("agent_sdk.runner", "run_agent"),
    "build_app_container": ("agent_sdk.bootstrap", "build_app_container"),
    "scan_and_load_features": ("agent_sdk.bootstrap", "scan_and_load_features"),
    "register_message_reaction_handlers": (
        "agent_sdk.bootstrap",
        "register_message_reaction_handlers",
    ),
    "Settings": ("agent_sdk.layer4_frameworks.config.app_config", "Settings"),
    "settings": ("agent_sdk.layer4_frameworks.config.app_config", "settings"),
    "HanaCredentials": (
        "agent_sdk.layer4_frameworks.config.vcap_util",
        "HanaCredentials",
    ),
    "get_hana_credentials": (
        "agent_sdk.layer4_frameworks.config.vcap_util",
        "get_hana_credentials",
    ),
    "AgentInfo": ("agent_sdk.layer1_domain.entities.agent_info", "AgentInfo"),
    "AgentRequest": ("agent_sdk.layer1_domain.entities.agent_request", "AgentRequest"),
    "AgentResponse": (
        "agent_sdk.layer1_domain.entities.agent_response",
        "AgentResponse",
    ),
    "AgentRegistration": (
        "agent_sdk.layer1_domain.entities.agent_registration",
        "AgentRegistration",
    ),
    "ToolRegistration": (
        "agent_sdk.layer1_domain.entities.tool_registration",
        "ToolRegistration",
    ),
    "ToolRegistrar": (
        "agent_sdk.layer4_frameworks.registry.tool_registrar",
        "ToolRegistrar",
    ),
    "AgentRuntimeConfig": (
        "agent_sdk.layer1_domain.entities.agent_runtime_config",
        "AgentRuntimeConfig",
    ),
    "ExecutionPolicy": (
        "agent_sdk.layer1_domain.entities.execution_policy",
        "ExecutionPolicy",
    ),
    "QueueMetadata": (
        "agent_sdk.layer1_domain.entities.queue_metadata",
        "QueueMetadata",
    ),
    "AgentBaseState": (
        "agent_sdk.layer1_domain.entities.agent_state",
        "AgentBaseState",
    ),
    "SharedStateDocument": (
        "agent_sdk.layer1_domain.entities.shared_state",
        "SharedStateDocument",
    ),
    "SharedStateLockInfo": (
        "agent_sdk.layer1_domain.entities.shared_state",
        "SharedStateLockInfo",
    ),
    "SharedStateRecord": (
        "agent_sdk.layer1_domain.entities.shared_state",
        "SharedStateRecord",
    ),
    "InboxRecord": (
        "agent_sdk.layer1_domain.entities.inbox_record",
        "InboxRecord",
    ),
    "OutboxRecord": (
        "agent_sdk.layer1_domain.entities.outbox_record",
        "OutboxRecord",
    ),
    "TenantContext": (
        "agent_sdk.layer1_domain.entities.tenant_context",
        "TenantContext",
    ),
    "ConversationMetadata": (
        "agent_sdk.layer1_domain.entities.conversation_metadata",
        "ConversationMetadata",
    ),
    "EventEnvelope": (
        "agent_sdk.layer1_domain.entities.event_envelope",
        "EventEnvelope",
    ),
    "UiEventType": ("agent_sdk.layer1_domain.entities.ui_events", "UiEventType"),
    "UiPayloadType": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "UiPayloadType",
    ),
    "WfInfo": ("agent_sdk.layer1_domain.entities.ui_events", "WfInfo"),
    "ProgressingCollapsePayload": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "ProgressingCollapsePayload",
    ),
    "ToolFormPayload": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "ToolFormPayload",
    ),
    "TextPayload": ("agent_sdk.layer1_domain.entities.ui_events", "TextPayload"),
    "ButtonAction": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "ButtonAction",
    ),
    "ButtonGroupPayload": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "ButtonGroupPayload",
    ),
    "OpenWorkspacePayload": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "OpenWorkspacePayload",
    ),
    "SummaryPayload": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "SummaryPayload",
    ),
    "ChatThinkingEvent": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "ChatThinkingEvent",
    ),
    "ChatResponseEvent": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "ChatResponseEvent",
    ),
    "ChatDisabledEvent": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "ChatDisabledEvent",
    ),
    "ChatEnabledEvent": (
        "agent_sdk.layer1_domain.entities.ui_events",
        "ChatEnabledEvent",
    ),
    "OrchestrationEventType": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "OrchestrationEventType",
    ),
    "TaskDefinition": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "TaskDefinition",
    ),
    "StepDefinition": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "StepDefinition",
    ),
    "ExecutorStep": (
        "agent_sdk.layer1_domain.entities.executor_step_contract",
        "ExecutorStep",
    ),
    "ExecutorStepEventPayload": (
        "agent_sdk.layer1_domain.entities.executor_step_contract",
        "ExecutorStepEventPayload",
    ),
    "CONVERSATION_PLAN_CREATED": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "CONVERSATION_PLAN_CREATED",
    ),
    "AGENT_PLAN_CREATED": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "AGENT_PLAN_CREATED",
    ),
    "AGENT_PLAN_EXECUTING": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "AGENT_PLAN_EXECUTING",
    ),
    "AGENT_PLAN_EXECUTED": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "AGENT_PLAN_EXECUTED",
    ),
    "AGENT_REQUEST_AGENT": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "AGENT_REQUEST_AGENT",
    ),
    "AGENT_PLAN_SUCCESS": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "AGENT_PLAN_SUCCESS",
    ),
    "AGENT_PLAN_ERROR": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "AGENT_PLAN_ERROR",
    ),
    "EXECUTOR_REQUEST_STEP_BATCH": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "EXECUTOR_REQUEST_STEP_BATCH",
    ),
    "EXECUTOR_REQUEST_AGENT": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "EXECUTOR_REQUEST_AGENT",
    ),
    "AGENT_STEP_STATUS": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "AGENT_STEP_STATUS",
    ),
    "EXECUTOR_STEP_STATUS": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "EXECUTOR_STEP_STATUS",
    ),
    "create_conversation_plan_created_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_conversation_plan_created_event",
    ),
    "create_agent_plan_created_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_agent_plan_created_event",
    ),
    "create_agent_plan_executing_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_agent_plan_executing_event",
    ),
    "create_agent_plan_executed_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_agent_plan_executed_event",
    ),
    "create_agent_request_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_agent_request_event",
    ),
    "create_agent_plan_success_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_agent_plan_success_event",
    ),
    "create_agent_plan_error_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_agent_plan_error_event",
    ),
    "create_executor_request_step_batch_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_executor_request_step_batch_event",
    ),
    "create_executor_request_agent_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_executor_request_agent_event",
    ),
    "create_agent_step_status_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_agent_step_status_event",
    ),
    "create_executor_step_status_event": (
        "agent_sdk.layer1_domain.entities.orchestration_events",
        "create_executor_step_status_event",
    ),
    "HitlInterruptPayload": (
        "agent_sdk.layer1_domain.entities.hitl_interrupt_payload",
        "HitlInterruptPayload",
    ),
    "InterruptType": (
        "agent_sdk.layer1_domain.entities.hitl_interrupt_payload",
        "InterruptType",
    ),
    "HitlResumeCommand": (
        "agent_sdk.layer1_domain.entities.hitl_resume_command",
        "HitlResumeCommand",
    ),
    "WorkflowEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "WorkflowEvent",
    ),
    "EVENT_WORKFLOW_STARTED": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_WORKFLOW_STARTED",
    ),
    "EVENT_UI_RENDER_REQUEST": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_UI_RENDER_REQUEST",
    ),
    "EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST",
    ),
    "EVENT_NODE_STARTED": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_NODE_STARTED",
    ),
    "EVENT_INPUT_VALIDATING": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_INPUT_VALIDATING",
    ),
    "EVENT_NODE_WAITING_USER": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_NODE_WAITING_USER",
    ),
    "EVENT_INPUT_UPDATED": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_INPUT_UPDATED",
    ),
    "EVENT_NODE_COMPLETED": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_NODE_COMPLETED",
    ),
    "EVENT_WORKFLOW_COMPLETED": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_WORKFLOW_COMPLETED",
    ),
    "EVENT_WORKFLOW_FAILED": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_WORKFLOW_FAILED",
    ),
    "EVENT_UNSUPPORTED_FEATURE": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_UNSUPPORTED_FEATURE",
    ),
    "EVENT_NODE_UPDATED": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_NODE_UPDATED",
    ),
    "EVENT_NODE_DATA_INITIALIZED": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_NODE_DATA_INITIALIZED",
    ),
    "EVENT_AGENT_SELECTED": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_AGENT_SELECTED",
    ),
    "EVENT_TOOL_SELECTED": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "EVENT_TOOL_SELECTED",
    ),
    "WorkflowStartedEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "WorkflowStartedEvent",
    ),
    "UiRenderRequestEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "UiRenderRequestEvent",
    ),
    "WorkflowGuidelineRenderRequestEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "WorkflowGuidelineRenderRequestEvent",
    ),
    "NodeStartedEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "NodeStartedEvent",
    ),
    "InputValidatingEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "InputValidatingEvent",
    ),
    "NodeWaitingUserEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "NodeWaitingUserEvent",
    ),
    "InputUpdatedEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "InputUpdatedEvent",
    ),
    "NodeCompletedEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "NodeCompletedEvent",
    ),
    "WorkflowCompletedEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "WorkflowCompletedEvent",
    ),
    "WorkflowFailedEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "WorkflowFailedEvent",
    ),
    "UnsupportedFeatureEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "UnsupportedFeatureEvent",
    ),
    "NodeUpdatedEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "NodeUpdatedEvent",
    ),
    "NodeDataInitializedEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "NodeDataInitializedEvent",
    ),
    "AgentSelectedEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "AgentSelectedEvent",
    ),
    "ToolSelectedEvent": (
        "agent_sdk.layer1_domain.entities.workflow_event",
        "ToolSelectedEvent",
    ),
    "AgentSDKError": ("agent_sdk.layer1_domain.exceptions", "AgentSDKError"),
    "GraphCompilationError": (
        "agent_sdk.layer1_domain.exceptions",
        "GraphCompilationError",
    ),
    "NodeExecutionError": ("agent_sdk.layer1_domain.exceptions", "NodeExecutionError"),
    "RegistrationError": ("agent_sdk.layer1_domain.exceptions", "RegistrationError"),
    "DependencyError": ("agent_sdk.layer1_domain.exceptions", "DependencyError"),
    "BusinessRuleException": (
        "agent_sdk.layer1_domain.exceptions",
        "BusinessRuleException",
    ),
    "WorkflowKnownIssueException": (
        "agent_sdk.layer1_domain.exceptions",
        "WorkflowKnownIssueException",
    ),
    "DelegationTimeoutException": (
        "agent_sdk.layer1_domain.exceptions",
        "DelegationTimeoutException",
    ),
    "QueueDeliveryException": (
        "agent_sdk.layer1_domain.exceptions",
        "QueueDeliveryException",
    ),
    "AgentStatus": (
        "agent_sdk.layer1_domain.value_objects.agent_status",
        "AgentStatus",
    ),
    "TransportState": (
        "agent_sdk.layer1_domain.value_objects.transport_state",
        "TransportState",
    ),
    "TransportSource": (
        "agent_sdk.layer1_domain.value_objects.transport_source",
        "TransportSource",
    ),
    "MessageTypeSet": (
        "agent_sdk.layer1_domain.value_objects.message_type_set",
        "MessageTypeSet",
    ),
    "NodeType": ("agent_sdk.layer1_domain.value_objects.node_type", "NodeType"),
    "AgentCallRequest": (
        "agent_sdk.layer1_domain.entities.agent_call",
        "AgentCallRequest",
    ),
    "AgentCallResult": (
        "agent_sdk.layer1_domain.entities.agent_call",
        "AgentCallResult",
    ),
    "ILogger": ("agent_sdk.layer2_application.interfaces.observability", "ILogger"),
    "IMonitor": ("agent_sdk.layer2_application.interfaces.observability", "IMonitor"),
    "IAgentRegistry": (
        "agent_sdk.layer2_application.interfaces.agent_registry",
        "IAgentRegistry",
    ),
    "IToolRegistry": (
        "agent_sdk.layer2_application.interfaces.tool_registry",
        "IToolRegistry",
    ),
    "IAgentPipeline": (
        "agent_sdk.layer2_application.interfaces.agent_pipeline",
        "IAgentPipeline",
    ),
    "IChatCompletionService": (
        "agent_sdk.layer2_application.interfaces.chat_completion_service",
        "IChatCompletionService",
    ),
    "ILLMService": (
        "agent_sdk.layer2_application.interfaces.llm_service",
        "ILLMService",
    ),
    "IInputGuard": (
        "agent_sdk.layer2_application.interfaces.input_guard",
        "IInputGuard",
    ),
    "IOutputGuard": (
        "agent_sdk.layer2_application.interfaces.output_guard",
        "IOutputGuard",
    ),
    "IMessagePublisher": (
        "agent_sdk.layer2_application.interfaces.message_publisher",
        "IMessagePublisher",
    ),
    "IMessageConsumer": (
        "agent_sdk.layer2_application.interfaces.message_consumer",
        "IMessageConsumer",
    ),
    "IWorkflowEventEmitter": (
        "agent_sdk.layer2_application.interfaces.workflow_event_emitter",
        "IWorkflowEventEmitter",
    ),
    "IAgentEndpointResolver": (
        "agent_sdk.layer2_application.interfaces.agent_endpoint_resolver",
        "IAgentEndpointResolver",
    ),
    "IAgentDelegator": (
        "agent_sdk.layer2_application.interfaces.agent_delegator",
        "IAgentDelegator",
    ),
    "IMCPClientService": (
        "agent_sdk.layer2_application.interfaces.mcp_client",
        "IMCPClientService",
    ),
    "IMessageDelivery": (
        "agent_sdk.layer2_application.interfaces.message_delivery",
        "IMessageDelivery",
    ),
    "ISharedStateRepository": (
        "agent_sdk.layer2_application.interfaces.shared_state_repository",
        "ISharedStateRepository",
    ),
    "IInboxRepository": (
        "agent_sdk.layer2_application.interfaces.inbox_repository",
        "IInboxRepository",
    ),
    "IOutboxRepository": (
        "agent_sdk.layer2_application.interfaces.outbox_repository",
        "IOutboxRepository",
    ),
    "MCPClientService": (
        "agent_sdk.layer4_frameworks.mcp.mcp_client_service",
        "MCPClientService",
    ),
    "ExecuteAgentInput": (
        "agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case",
        "ExecuteAgentInput",
    ),
    "ExecuteAgentOutput": (
        "agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case",
        "ExecuteAgentOutput",
    ),
    "ResumeAgentInput": (
        "agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case",
        "ResumeAgentInput",
    ),
    "ResumeAgentOutput": (
        "agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case",
        "ResumeAgentOutput",
    ),
    "ResumeAgentUseCase": (
        "agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case",
        "ResumeAgentUseCase",
    ),
    "AgentGraphBuilder": (
        "agent_sdk.layer4_frameworks.graph.agent_graph_builder",
        "AgentGraphBuilder",
    ),
    "FlowGraphBuilder": (
        "agent_sdk.layer4_frameworks.graph.flow_graph_builder",
        "FlowGraphBuilder",
    ),
    "ToolAgentBuilder": (
        "agent_sdk.layer4_frameworks.graph.tool_agent_builder",
        "ToolAgentBuilder",
    ),
    "ToolAgentState": (
        "agent_sdk.layer4_frameworks.graph.tool_agent_state",
        "ToolAgentState",
    ),
    "StateGraph": ("langgraph.graph", "StateGraph"),
    "END": ("langgraph.graph", "END"),
    "ToolNode": ("langgraph.prebuilt", "ToolNode"),
    "tools_condition": ("langgraph.prebuilt", "tools_condition"),
    "interrupt": ("langgraph.types", "interrupt"),
    "FlowConfig": ("agent_sdk.layer1_domain.entities.flow_config", "FlowConfig"),
    "StepConfig": ("agent_sdk.layer1_domain.entities.flow_config", "StepConfig"),
    "NodeTypeRegistry": (
        "agent_sdk.layer2_application.services.node_type_registry",
        "NodeTypeRegistry",
    ),
    "run_consumer_agent": (
        "agent_sdk.layer3_adapters.presenters.agent_consumer",
        "run_consumer_agent",
    ),
    "input_guard_node": (
        "agent_sdk.layer2_application.services.middleware",
        "input_guard_node",
    ),
    "output_guard_node": (
        "agent_sdk.layer2_application.services.middleware",
        "output_guard_node",
    ),
    "error_handler_node": (
        "agent_sdk.layer2_application.services.middleware",
        "error_handler_node",
    ),
    "format_response_node": (
        "agent_sdk.layer2_application.services.middleware",
        "format_response_node",
    ),
    "OpenAIService": ("agent_sdk.layer4_frameworks.ai.openai_service", "OpenAIService"),
    "LangChainLLMService": (
        "agent_sdk.layer4_frameworks.ai.openai_service",
        "LangChainLLMService",
    ),
    "make_openai_service": (
        "agent_sdk.layer4_frameworks.ai.llm_factory",
        "make_openai_service",
    ),
    "make_llm_service": (
        "agent_sdk.layer4_frameworks.ai.llm_factory",
        "make_llm_service",
    ),
    "tool": ("agent_sdk.layer4_frameworks.ai.local_tool", "tool"),
    "RemoteAgentTool": (
        "agent_sdk.layer4_frameworks.ai.remote_agent_tool",
        "RemoteAgentTool",
    ),
    "default_tool_factory": (
        "agent_sdk.layer4_frameworks.ai.agent_tool_factory",
        "default_tool_factory",
    ),
    "HttpAgentRegistry": (
        "agent_sdk.layer4_frameworks.registry.http_agent_registry",
        "HttpAgentRegistry",
    ),
    "RegistryEndpointResolver": (
        "agent_sdk.layer4_frameworks.registry.registry_endpoint_resolver",
        "RegistryEndpointResolver",
    ),
    "HanaCheckpointSaver": (
        "agent_sdk.layer4_frameworks.persistence.checkpoint_store",
        "HanaCheckpointSaver",
    ),
    "HanaSharedStateRepository": (
        "agent_sdk.layer4_frameworks.persistence.hana.hana_shared_state_repository",
        "HanaSharedStateRepository",
    ),
    "HanaInboxRepository": (
        "agent_sdk.layer4_frameworks.persistence.hana.hana_inbox_repository",
        "HanaInboxRepository",
    ),
    "HanaOutboxRepository": (
        "agent_sdk.layer4_frameworks.persistence.hana.hana_outbox_repository",
        "HanaOutboxRepository",
    ),
    "create_checkpointer": (
        "agent_sdk.layer4_frameworks.persistence.checkpoint_store",
        "create_checkpointer",
    ),
    "ensure_definition": (
        "agent_sdk.layer2_application.utils.definition",
        "ensure_definition",
    ),
    "DependencyResolver": (
        "agent_sdk.layer2_application.utils.dependency_resolver",
        "DependencyResolver",
    ),
    "ensure_dependency_resolver": (
        "agent_sdk.layer2_application.utils.dependency_resolver",
        "ensure_dependency_resolver",
    ),
    "StateResolver": (
        "agent_sdk.layer2_application.utils.state_resolver",
        "StateResolver",
    ),
    "ensure_state_resolver": (
        "agent_sdk.layer2_application.utils.state_resolver",
        "ensure_state_resolver",
    ),
    "HanaConnectionManager": (
        "agent_sdk.layer4_frameworks.persistence.hana.hana_connection_manager",
        "HanaConnectionManager",
    ),
    "create_agent_app": (
        "agent_sdk.layer3_adapters.presenters.agent_server",
        "create_agent_app",
    ),
    "HumanMessage": ("langchain_core.messages", "HumanMessage"),
    "SystemMessage": ("langchain_core.messages", "SystemMessage"),
    "AIMessage": ("langchain_core.messages", "AIMessage"),
    "WorkflowEventEmitter": (
        "agent_sdk.layer2_application.services.workflow_event_emitter",
        "WorkflowEventEmitter",
    ),
    "OutboxPublisher": (
        "agent_sdk.layer2_application.services.outbox_publisher",
        "OutboxPublisher",
    ),
    "DurableMessageDeduplicator": (
        "agent_sdk.layer2_application.services.durable_message_deduplicator",
        "DurableMessageDeduplicator",
    ),
    "extract_state_snapshot": (
        "agent_sdk.layer2_application.utils.state_snapshot",
        "extract_state_snapshot",
    ),
    "merge_state_snapshot": (
        "agent_sdk.layer2_application.utils.state_snapshot",
        "merge_state_snapshot",
    ),
    "serialize_event": (
        "agent_sdk.layer2_application.services.workflow_event_emitter",
        "serialize_event",
    ),
    "workflow_event_scope": (
        "agent_sdk.layer2_application.services.workflow_event_runtime",
        "workflow_event_scope",
    ),
    "emit_workflow_event": (
        "agent_sdk.layer2_application.services.workflow_event_runtime",
        "emit_workflow_event",
    ),
    "AgentCallCoordinator": (
        "agent_sdk.layer4_frameworks.http.agent_call_coordinator",
        "AgentCallCoordinator",
    ),
    "ContextBudgetManager": (
        "agent_sdk.layer4_frameworks.ai.context_budget",
        "ContextBudgetManager",
    ),
    "ContextBudgetConfig": (
        "agent_sdk.layer1_domain.entities.context_config",
        "ContextBudgetConfig",
    ),
    "CompactionStrategy": (
        "agent_sdk.layer1_domain.entities.context_config",
        "CompactionStrategy",
    ),
    "PayloadType": ("agent_sdk.layer1_domain.entities.context_config", "PayloadType"),
    "AgentCapability": (
        "agent_sdk.layer1_domain.entities.agent_capability",
        "AgentCapability",
    ),
    "AgentDiscoveryService": (
        "agent_sdk.layer2_application.services.agent_discovery_service",
        "AgentDiscoveryService",
    ),
    "AsyncAgentDelegator": (
        "agent_sdk.layer2_application.services.async_agent_delegator",
        "AsyncAgentDelegator",
    ),
    "MessageReactionRouter": (
        "agent_sdk.layer2_application.services.message_reaction.router",
        "MessageReactionRouter",
    ),
    "MessageHandlingResult": (
        "agent_sdk.layer1_domain.entities.message_handling_result",
        "MessageHandlingResult",
    ),
    "LLMUsageRecord": (
        "agent_sdk.layer1_domain.entities.llm_usage",
        "LLMUsageRecord",
    ),
    "LLMCostCalculator": (
        "agent_sdk.layer2_application.services.llm_cost_calculator",
        "LLMCostCalculator",
    ),
    "LoggingLLMUsageRecorder": (
        "agent_sdk.layer2_application.services.llm_usage_recorder",
        "LoggingLLMUsageRecorder",
    ),
    "UsageTrackingChatModel": (
        "agent_sdk.layer4_frameworks.ai.usage_tracking_chat_model",
        "UsageTrackingChatModel",
    ),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'agent_sdk' has no attribute {name!r}")


def __dir__() -> list[str]:
    return __all__
