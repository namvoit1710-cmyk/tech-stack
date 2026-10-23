import { IWorkflowSchemaField } from "@/features/workflows/types/workflows";

export interface INodeDefinition {
    id: string;
    tenant_id: string;
    name: string;
    description?: string;
    base_type: string;
    base_worker_id: string;
    parent_definition_id?: string | null;
    locked_defaults?: Record<string, any>;
    input_schema?: IWorkflowSchemaField[];
    output_schema?: IWorkflowSchemaField[];
    input_mapping?: Record<string, any>;
    output_mapping?: Record<string, any>;
    icon?: string;
    color?: string;
    tags?: string[];
    kind?: string;
    collection?: string;
    data_mode?: string;
    edit_mode?: string;
    created_at?: string;
    updated_at?: string;
}

// List
export interface INodeDefinitionParams {
    tenant_id?: string;
    page?: number;
    limit?: number;
    q?: string;
}

export interface INodeDefinitionListResponse {
    status: string;
    data: {
        items: INodeDefinition[];
        total: number;
    };
}

// Detail
export interface INodeDefinitionDetailResponse {
    ok: boolean;
    data: INodeDefinition;
}

// Create
export interface ICreateNodeDefinitionPayload {
    tenant_id: string;
    name: string;
    description?: string;
    base_type: string;
    base_worker_id: string;
    parent_definition_id?: string | null;
    locked_defaults?: Record<string, any>;
    input_schema?: IWorkflowSchemaField[];
    output_schema?: IWorkflowSchemaField[];
    input_mapping?: Record<string, any>;
    output_mapping?: Record<string, any>;
    icon?: string;
    color?: string;
    tags?: string[];
    kind?: string;
    collection?: string;
}

// Update
export interface IUpdateNodeDefinitionPayload {
    name?: string;
    description?: string;
    locked_defaults?: Record<string, any>;
    input_schema?: IWorkflowSchemaField[];
    output_schema?: IWorkflowSchemaField[];
    input_mapping?: Record<string, any>;
    output_mapping?: Record<string, any>;
    icon?: string;
    color?: string;
    tags?: string[];
    kind?: string;
    collection?: string;
}

// Test
export interface ITestNodeDefinitionPayload {
    input_data: Record<string, any>;
}

export interface ITestNodeDefinitionDataResponse {
    status: string;
    data: ITestNodeDefinitionResponse
}

export interface ITestNodeDefinitionResponse {
    node_definition_id: string;
    worker_type: string;
    resolved_input: Record<string, any>;
    status: string;
    outputs: Record<string, any>;
    output_reference?: string;
    execution_time_ms: number;
    error?: string;
}
