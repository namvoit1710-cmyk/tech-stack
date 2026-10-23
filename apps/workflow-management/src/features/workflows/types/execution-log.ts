export interface IExecutionLogSummary {
  run_id: string;
  workflow_id: string;
  status: string;
  started_at: string;
  completed_at: string;
  run_ids: string[];
  total_runs: number;
  total_events: number;
  node_counts: {
    completed: number;
    failed: number;
    skipped: number;
  };
}

export interface IExecutionLogEvent {
  index: number;
  run_id: string;
  depth: number;
  sequence: number;
  event_id: string;
  event_type: string;
  topic: string;
  timestamp: string;
  causation_id: string;
  payload: Record<string, unknown>;
}

export interface IExecutionLogDetailResponse {
  status: string;
  data: {
    summary: IExecutionLogSummary;
    events: IExecutionLogEvent[];
  };
}
