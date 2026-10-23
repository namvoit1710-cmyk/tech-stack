// SA-1372 Replay Debugger — response/request types (mirror the backend
// replay_debugger feature). The API SDK returns the full {status, data}
// envelope, matching IExecutionLogDetailResponse.

export interface IReplayTimelineEntry {
  index: number;
  sequence: number;
  event_type: string;
  node_id: string | null;
  topic: string;
  is_error: boolean;
}

export interface IReplayNodeError {
  error_code?: string | null;
  error_message?: string | null;
}

export interface IReplayNodeIO {
  input: Record<string, unknown> | null;
  state_updates: Record<string, unknown> | null;
  output: unknown;
  output_port: string | null;
  taken_edge_ports: Record<string, string> | null;
}

export interface IReplayOpenData {
  replay_session_id: string;
  run_id: string;
  cursor: number;
  total_steps: number;
  // First page only (TIMELINE_PAGE_SIZE) — further pages via the timeline endpoint.
  timeline: IReplayTimelineEntry[];
}

// GET /runs/{id}/replay/{session}/timeline?offset=&limit= — one page of the
// timeline; `total` mirrors total_steps (execution-log paging convention).
export interface IReplayTimelinePageData {
  replay_session_id: string;
  run_id: string;
  items: IReplayTimelineEntry[];
  total: number;
  offset: number;
  limit: number;
}

export interface IReplayTimelinePageResponse {
  status: string;
  data: IReplayTimelinePageData;
}

export interface IReplayStateData {
  replay_session_id: string;
  run_id: string;
  cursor: number;
  total_steps: number;
  node_id: string | null;
  event: {
    sequence: number;
    event_type: string;
    timestamp: string;
    payload: Record<string, unknown>;
  };
  scope_chain: Record<string, unknown>;
  node_io: IReplayNodeIO;
  loop_ctx: Record<string, unknown>;
  completed_nodes: string[];
  skipped_nodes: string[];
  // Per-node status at the cursor for the canvas overlay (n8n-style ✓/✗).
  node_status: Record<string, string>;
  // Current node's failure, surfaced inline (n8n-style), if it failed by here.
  node_error: IReplayNodeError | null;
  redacted_keys: string[];
  replay_complete: boolean;
}

export interface IReplayOpenResponse {
  status: string;
  data: IReplayOpenData;
}

export interface IReplayStateResponse {
  status: string;
  data: IReplayStateData;
}

export interface IReplayCloseResponse {
  status: string;
  data: { replay_session_id: string; closed: boolean };
}

export type ReplayStepOp = "step" | "run_to" | "index" | "pause";

export interface IReplayStepPayload {
  op: ReplayStepOp;
  n?: number;
  node_id?: string | null;
  index?: number | null;
}
