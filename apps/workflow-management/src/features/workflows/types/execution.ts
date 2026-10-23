
export interface IWorkflowExecutionParams {
    $skip?: number;
    $top?: number;
    $filter?: string;
    $orderby?: string;
    $count?: boolean;
    offset?: number;
    limit?: number;
    // Top-level runs only; sub-workflow runs are loaded on demand per row.
    roots_only?: boolean;
    // SA-2704: pin the list to a single run by id (shareable deep link). When
    // set, the backend returns just that run (root or sub-run) and roots_only is
    // ignored so a linked child run still resolves.
    run_id?: string;
}

export interface IWorkflowExecutionOutputSummary {
    total_items: number;
    preview: Record<string, any>;
}

export interface IWorkflowExecutionOutputRef {
    output_id: string;
    chunk_size: number;
}

export interface IWorkflowExecutionOutput {
    type: string;
    summary: IWorkflowExecutionOutputSummary;
    ref: IWorkflowExecutionOutputRef;
}

export interface IWorkflowExecutionErrorMessage {
    message: string;
    at: number;
}

export interface IWorkflowExecutionError {
    node_id: string;
    code: string;
    messages: IWorkflowExecutionErrorMessage[];
}

// SA-2558: server-verified identity of whoever (or whatever) triggered a run.
// Resolved on the backend from the BFF session / Bearer JWT at run creation;
// the FE only renders it. ``is_system`` marks automated / M2M / schedule runs
// (and pre-migration rows) so the row shows a neutral "System" chip.
export interface ITriggeredBy {
    id: string;
    label: string;
    is_system: boolean;
}

// SA-2719: the Environment Collection a run resolved its {{$env.KEY}} references
// from, snapshotted on the run at creation (id + name + section) so the chip
// stays truthful even after the environment is renamed/deleted. ``tier`` is a
// backend-derived colour hint for the chip dot (prod/qas/stg/dev/other). Null on
// the run means no environment resolved (unbound flow / no {{$env}} / historical
// row) — the FE then shows a neutral "No environment" chip. FE renders only.
export interface IEnvironment {
    id: string;
    name: string;
    section: string;
    tier: string;
}

export interface IWorkflowExecutionHistory {
    id: string;
    workflow_id: string;
    workflow_name: string;
    status: string;
    started_at: string | null;
    completed_at: string | null;
    error: any | null;
    is_test: boolean;
    tenant_id: string;
    parent_run_id?: string | null;
    root_run_id?: string;
    depth_level?: number;
    // SA-2650: live run-progress along the execution path — only populated for
    // RUNNING runs (null otherwise). Backend-computed; the FE only renders it.
    progress_pct?: number | null;
    completed_nodes?: number | null;
    total_nodes?: number | null;
    // SA-2558: who triggered this run (chip on the row).
    triggered_by?: ITriggeredBy | null;
    // SA-2719: which environment this run used (chip on the row). Null → the
    // run resolved no environment; the FE shows a neutral "No environment" chip.
    environment?: IEnvironment | null;
}

// One direct sub-workflow run, from GET /runs/{id}/children (run.context.child_runs).
export interface IRunChild {
    parent_node_id: string;
    child_run_id: string;
    workflow_id: string;
    status: string;
    started_at: string | null;
    completed_at: string | null;
    error: string | null;
    // SA-2650: live progress for a RUNNING subflow row (null otherwise).
    progress_pct?: number | null;
    completed_nodes?: number | null;
    total_nodes?: number | null;
    // SA-2558: who triggered this subflow run (null → inherits/system).
    triggered_by?: ITriggeredBy | null;
    // SA-2719: this subflow's own environment (inherited or per-subflow/dynamic
    // override). Null → neutral "No environment" chip. FE renders only.
    environment?: IEnvironment | null;
}

export interface IRunChildrenResponse {
    status: string;
    data: {
        run_id: string;
        children: IRunChild[];
    };
}

export interface IWorkflowExecutionHistoryResponse {
    status: string;
    data: {
        items: IWorkflowExecutionHistory[];
        total: number;
        $skip: number;
        $top: number;
    };
}


// Execute Node status types

export type NodeExecutionStatus = "idle" | "executing" | "completed" | "failed"

interface NodeOutputSummary {
    total_items: number
    preview: Record<string, unknown>
}

interface NodeOutputRef {
    output_id: string
    chunk_size: number
}

interface NodeOutput {
    type: string
    summary: NodeOutputSummary
    ref: NodeOutputRef
}

interface ExecutionRun {
    id: string
    status: string
    workflow_id: string
    workflow_name: string
}

export interface NodeExecutionOutput {
    $params: Record<string, unknown>
    $payload: Record<string, unknown>
    $outputs: Record<string, NodeOutput>
    $workflow: Record<string, unknown>
    $run: ExecutionRun
    $artifacts: Record<string, unknown>
    $input: Record<string, unknown>
}

export interface NodeExecutionState {
    status: NodeExecutionStatus
    sessionId?: string
    runId?: string
    cacheKey?: string
    timestamp?: number
    output?: NodeExecutionOutput
    hasLargeOutput?: boolean
}

export interface IResumeTaskPayload {
    updated_input?: Record<string, any>;
}

export interface INodeOutputPath {
    path: string;
    kind: string;
    output_type: string;
    item_type: string | null;
    sample: any;
    columns: any[];
    preview_rows: any[];
    label: string;
    expandable: boolean;
    display_path: string;
}

export interface INodeOutputScope {
    scope_type: string;
    scope_id: string;
    label: string;
    expression_prefix: string;
    paths: INodeOutputPath[];
    node_type?: string;
    has_file_data?: boolean;
    file_id?: string;
    columns?: string[];
    preview_rows?: Record<string, any>[];
    artifacts?: any[];
    default_artifact_path?: string | null;
}

export interface INodeOutputIteration {
    iteration_index: number | null;
    task_id: string;
    status: string;
    started_at: string | null;
    completed_at: string | null;
    output_port: string;
    taken_edge_ports: Record<string, any>;
    error: string | null;
    error_code: string;
    scopes: INodeOutputScope[];
}

export interface INodeOutputData {
    run_id: string;
    node_id: string;
    node_name: string;
    node_type: string;
    iterations: INodeOutputIteration[];
    node_summary: string | null;
    error: string | null;
    error_code: string;
    error_details: Record<string, any>;
}

export interface IGetNodeOutputResponse {
    status: string;
    data: INodeOutputData;
}

