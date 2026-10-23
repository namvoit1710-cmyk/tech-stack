import type { IWorkflowSessionData } from "./workflow-session";

type WorkflowUnknownRecord = Record<string, unknown>;

export interface IWorkflowNodeMetadata {
    groups?: string[];
    tags?: string[];
    categories?: string[];
}

export interface IWorkflowNodeParamBinding {
    key: string;
    type: string;
    value: unknown;
}

export type WorkflowStatus = "DRAFT" | "PUBLISHED";
export type PortDataType = "any" | "string" | "number" | "boolean" | "object" | "array";
export type NodeType =
    | "TASK"
    | "PARALLEL"
    | "CONDITION"
    | "LOOP"
    | "SWITCH"
    | "MERGE"
    | "TRIGGER"
    | "WORKFLOW"
    | "COMPUTE"
    | "GROUP"
    | "INPUT"
    | "HUMAN_ACTION";

export interface IWorkflowNodePorts {
    in?: IWorkflowNodePort[];
    out?: IWorkflowNodePort[];
}

export interface IWorkflowNodePort {
    id: string;
    label: string;
    data_type: PortDataType;
    required: boolean;
    description: string;
    readonly?: boolean;
}

export interface IWorkflowSchemaField {
    key: string;
    outputType?: string;
    items: IWorkflowListItem[];
    rules?: WorkflowUnknownRecord;
    [key: string]: unknown;
}

export interface IWorkflowNodeDefinitionSchemaField {
    key: string;
    type?: string;
    label?: string;
    required?: boolean;
    [key: string]: unknown;
}

export interface IWorkflowNodeDefinition {
    id: string;
    tenant_id?: string;
    name: string;
    description?: string;
    base_type: string;
    base_worker_id: string;
    locked_defaults?: WorkflowUnknownRecord;
    input_schema?: IWorkflowNodeDefinitionSchemaField[];
    output_schema?: IWorkflowNodeDefinitionSchemaField[];
    input_mapping?: WorkflowUnknownRecord;
    output_mapping?: WorkflowUnknownRecord;
    icon?: string;
    color?: string;
    tags?: string[];
    data_mode?: string;
    edit_mode?: string;
}

export interface IWorkflowNode {
    id: string;
    name: string;
    type?: NodeType;
    node_type?: NodeType;
    node_class?: string;
    description?: string;
    instruction?: string;
    icon?: string;
    color?: string;
    tags?: string[];
    worker_type?: string | null;
    node_definition_id?: string | null;
    parameters?: WorkflowUnknownRecord;
    ports?: IWorkflowNodePorts;
    x?: number | null;
    y?: number | null;
}

export interface IWorkflowEdge {
    id: string;
    source_node_id?: string;
    target_node_id?: string;
    condition?: string | null;
    source_port_id?: string;
    target_port_id?: string;
    label?: string;
    description?: string;
}

export interface IWorkflow {
    id: string;
    name: string;
    main_flow?: boolean;
    version: string;
    description?: string;
    status: WorkflowStatus;
    input_schema?: IWorkflowSchemaField[];
    output_schema?: IWorkflowSchemaField[];
    nodes: IWorkflowNode[];
    edges: IWorkflowEdge[];
    purpose?: string;
    metadata?: WorkflowUnknownRecord;
    created_at?: string;
    updated_at?: string;
    schema_version?: string;
    workflowSession?: IWorkflowSessionData;
    test_run_id?: string;
    routing_path?: string;
    // SA-2570: attribution. created_by is the immutable creator; owner is the
    // reassignable owner. Both are user ids (Keycloak sub) resolved to a display
    // name via the known-users list (GET /users).
    created_by?: string | null;
    updated_by?: string | null;
    owner?: string | null;
    // Resolved server-side from Keycloak (so the list needs no /users pre-fetch).
    owner_name?: string | null;
    // SA-2751: manual time saved. BE computes; FE only renders the chip.
    // saved_seconds = manual_seconds × successful_runs (COMPLETED, non-test, all
    // versions). manual_seconds null / saved_seconds 0 → no chip.
    manual_work?: {
        manual_seconds: number | null;
        successful_runs: number;
        saved_seconds: number;
    };
}

// SA-2576: a known user of the workflow app (owner-picker option).
export interface IWorkflowUser {
    id: string;
    email?: string | null;
    name: string;
}

// Wrapped in the standard response envelope ({ status, data }) like the list.
export interface IWorkflowUsersResponse {
    status: string;
    data: {
        items: IWorkflowUser[];
        total: number;
    };
}

export interface IWorkflowVersionItem {
    id: string;
    version: string;
    status: "draft" | "published" | "archived";
    isCurrent: boolean;
    isPublished: boolean;
    createdAt?: string;
    updatedAt?: string;
}

export interface IWorkflowVersionSummary {
    currentVersion: string | null;
    publishVersion: string | null;
    versions: IWorkflowVersionItem[];
}

export interface IWorkflowVersionListResponse {
    ok: boolean;
    data: IWorkflowVersionSummary;
}

export interface IWorkflowVersionDetailParams {
    workflowId: string;
    versionId: string;
}

export interface IPublishWorkflowPayload {
    version: string;
    is_confirm?: boolean;
}

export interface IPublishWorkflowResponse {
    ok: boolean;
}

// Detail workflow
export interface IWorkflowDetailResponse {
    ok: boolean;
    data: IWorkflow;
}

// List workflow
export interface IWorkflowParams {
    $skip?: number;
    $top?: number;
    $filter?: string;
    $orderby?: string;
    $count?: boolean;
    offset?: number;
    limit?: number;
    // SA-2252: resolve each workflow's row to its PUBLISHED version when one exists
    // (else latest-edited), so the list shows the published status/version.
    prefer_published?: boolean;
}

export interface IWorkflowListVersionFields {
    current_version?: string | null;
    publish_version?: string | null;
}

export interface IWorkflowListItem extends IWorkflow, IWorkflowListVersionFields { }

export interface IWorkflowListResponse {
    status: string;
    data: {
        items: IWorkflowListItem[];
        total: number;
        $skip: number;
        $top: number;
    };
}

// Create workflow

export interface ICreateWorkflowPayload {
    name: string;
    description: string;
    input_schema?: IWorkflowSchemaField[];
    output_schema?: IWorkflowSchemaField[];
    nodes: IWorkflowNode[];
    edges: IWorkflowEdge[];
    metadata?: WorkflowUnknownRecord;
    schema_version?: string;
}

export interface ICreateWorkflowResponse {
    ok: boolean;
    data: IWorkflow;
}

// Create workflow with AI prompt
export interface ICreateWorkflowWithAIPayload {
    message: string;
    conv_id?: string;
    user_id?: string;
    tenant_id?: string;
    source?: string;
}

export interface IWorkflowSchema {
    name: string;
    description: string;
    input_schema: IWorkflowSchemaField[];
    output_schema: IWorkflowSchemaField[];
    nodes: IWorkflowNode[];
    edges: IWorkflowEdge[];
    metadata: WorkflowUnknownRecord;
    schema_version: string;
}

export interface IAgentData {
    content: string;
    status: string;
    missing_inputs: string[];
    workflow_schema: IWorkflowSchema;
    validation_errors: string[];
    workflow_id: string;
    conv_id?: string;
}

export interface ICreateWorkflowWithAIResponse {
    message: string;
    status: string;
    session_id: string;
    data: WorkflowUnknownRecord;
    agent_data: IAgentData;
    error: string | null;
    error_code: string | null;
    correlation_id: string | null;
    duration_ms: number;
    interrupted: boolean;
    interrupt_payload: unknown;
}

// Save workflow
export interface IWorkflowSaveNode {
    id: string;
    name: string;
    node_type?: NodeType;
    node_class?: string;
    worker_type?: string | null;
    node_definition_id?: string | null;

    parameters?: WorkflowUnknownRecord;

    ports?: IWorkflowNodePorts;
    x?: number | null;
    y?: number | null;

    description?: string;
    instruction?: string;
    icon?: string;
    color?: string;
    tags?: string[];
}

export interface IWorkflowSavePayload {
    id: string;
    name: string;
    description?: string;
    status: WorkflowStatus;
    purpose?: string;
    input_schema?: IWorkflowSchemaField[];
    output_schema?: IWorkflowSchemaField[];
    nodes: IWorkflowSaveNode[];
    edges: IWorkflowEdge[];
    metadata?: WorkflowUnknownRecord;
    routing_path?: string;
    version: string;
    is_confirm?: boolean;
    manual_seconds?: number;  // SA-2751
}

// Workflow Config
export interface IWorkflowConfigParameter {
    key: string;
    label: string;
    description: string;
    type: string;
    required: boolean;
    default: unknown;
}

export interface IWorkflowConfig {
    id: string;
    workflow_id: string;
    tenantId: string;
    parameters: IWorkflowConfigParameter[];
    metadata?: WorkflowUnknownRecord;
    created_at: number;
    updated_at: number;
}

export interface IVariableSuggestionPath {
    path: string;
    kind: string;
    output_type: string;
    item_type: string | null;
    sample: unknown;
    columns: string[];
    preview_rows: unknown[];
    label: string;
    expandable: boolean;
    display_path: string;
}

export interface IWorkflowArtifact {
    file_id: string;
    name: string;
    content_type?: string;
    artifact_type: string;
    row_count: number;
    links?: {
        data: string;
        patch: string;
    };
}

export interface IVariableSuggestionScope {
    scope_type: string;
    scope_id: string;
    label: string;
    expression_prefix: string;
    paths: IVariableSuggestionPath[];
    node_type: string | null;
    has_file_data: boolean;
    file_id: string | null;
    columns: string[];
    preview_rows: WorkflowUnknownRecord[];
    artifacts: IWorkflowArtifact[];
    default_artifact_path: string | null;
}

// Legacy types for backward compatibility
export interface IVariableSuggestionSource {
    node_id: string | null;
    node_name: string;
    node_type: string;
    expression_prefix: string;
    paths: IVariableSuggestionPath[];
    has_file_data: boolean;
    file_id: string | null;
    columns: string[];
    preview_rows: WorkflowUnknownRecord[];
    artifacts: IWorkflowArtifact[];
}

export interface IArtifactVariableSuggestionSource extends IVariableSuggestionSource {
    node_id: null;
    node_name: "artifacts";
    node_type: "";
    expression_prefix: "{{$artifacts";
}

export interface IVariableSuggestionData {
    run_id: string;
    node_id: string;
    workflow: unknown;
    run: unknown;
    scopes: IVariableSuggestionScope[];
    error: string | null;
    error_code: string;
    error_details: WorkflowUnknownRecord;
    // Legacy fields for backward compatibility
    artifacts?: IArtifactVariableSuggestionSource;
    sources?: IVariableSuggestionSource[];
}

export interface IVariableSuggestionResponse {
    status: string;
    data: IVariableSuggestionData;
}

// Design-time suggestions (GET /workflows/{id}/nodes/{id}/variable-suggestions).
// Unlike the run-scoped endpoint, this returns a FLAT, ranked `matches[]` (no runtime samples)
// plus per-scope summary counters. Normalized into IVariableSuggestionScope[] before rendering.
export interface IDesignTimeSuggestionMatch {
    scope_type: string;
    scope_id: string;
    scope_label: string;
    node_type: string | null;
    expression_prefix: string;
    path: string;
    kind: string;
    output_type: string;
    item_type: string | null;
    label: string;
    display_path: string;
    match_kind: string;
}

export interface IDesignTimeSuggestionScopeSummary {
    scope_type: string;
    scope_id: string;
    label: string;
    expression_prefix: string;
    match_count: number;
    returned_count: number;
    truncated: boolean;
}

export interface IDesignTimeSuggestionData {
    workflow_id: string;
    node_id: string;
    query: string;
    matches: IDesignTimeSuggestionMatch[];
    scopes: IDesignTimeSuggestionScopeSummary[];
    total_matches: number;
    truncated: boolean;
    error: string | null;
    error_code: string;
    error_details: WorkflowUnknownRecord;
}

export interface IDesignTimeSuggestionResponse {
    status: string;
    data: IDesignTimeSuggestionData;
}

export interface IExecuteAIPayload {
    message: string
    conv_id?: string
    user_id?: string
    tenant_id?: string
    source?: string
    correlation_id?: string
    parameters?: WorkflowUnknownRecord
    context?: WorkflowUnknownRecord
    reply_to?: string
    reply_topic?: string
    action?: string
    agent_type?: string
    intent?: WorkflowUnknownRecord
    execution_context?: WorkflowUnknownRecord
    context_snapshot?: WorkflowUnknownRecord
}

export interface IExecuteAIResponse {
    ok: boolean
    data?: unknown
    message?: string
}
