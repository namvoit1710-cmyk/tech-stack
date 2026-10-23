export type RerunStrategy = "none" | "node_only" | "cascade";

export interface Operation {
  operation: "insert" | "update" | "delete";
  row_id: string | null;
  data: Record<string, unknown>;
}

export interface ArtifactOperation {
  artifact_id: string;
  operations: Operation[];
}

export interface PatchDataRequest {
  artifacts?: ArtifactOperation[];
}

interface OpResult {
  op_index: number;
  status: "ok" | "failed";
  rows_affected?: number;
  row_ids?: string[];
}

export interface PatchDataResponse {
  etag: string;
  version: number;
  file_lineage: Record<string, string>;
  results: OpResult[];
  rerun: {
    strategy: RerunStrategy;
    triggered: boolean;
    degraded_from: RerunStrategy | null;
    reset_node_ids: string[];
    task_ids: string[];
  };
}

// --- Mutation params ---

export interface PatchNodeDataParams {
  run_id: string;
  node_id: string;
  task_id?: string;
  etag?: string;
  data: PatchDataRequest;
}
