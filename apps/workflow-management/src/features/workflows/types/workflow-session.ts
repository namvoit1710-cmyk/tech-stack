export interface IWorkflowSessionStagedNode {
    node_id: string;
    cache_key: string;
    has_preview: boolean;
    artifacts_count: number;
    updated_at: number;
    input_hash: string;
    output_hash: string;
    needs_rerun: boolean;
}

export interface IWorkflowSessionParameters {
    template_fields_interface: string;
    template_fields: any[];
    criteria_selection_interface: string;
    criteria_selection: any[];
    template_source_file: string;
    allow_data_src: string;
    object_type_id: string;
    template_change_type: string;
    description: string;
    name: string;
    rules: string;
    [key: string]: any;
}

export interface IWorkflowSessionData {
    session_id: string;
    run_id: string;
    status: "running" | "completed" | "failed" | "idle" | string;
    workflow_id: string;
    parameters: IWorkflowSessionParameters;
    overrides: any[];
    staged: IWorkflowSessionStagedNode[];
}

export interface IWorkflowSessionResponse {
    ok: boolean;
    data: IWorkflowSessionData;
}
