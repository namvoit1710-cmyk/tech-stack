import type { IField } from "@ldc/autoform";
export type { IApprovalFlowEdge, IApprovalFlowNode, IApprovalFlowPayload, IInputRequestedPayload } from "@ldc/form-tool";
import type { IWorkerAppHealthPayload } from "@/features/workers/types/worker-app";

export type BaseSocketEvent = ISocketEventMeta

export enum SocketEvent {
  // run lifecycle (topic with status payload)
  RunStatus = "run.status",

  // node dispatch
  NodeDispatched = "node.dispatched",

  // node terminal
  NodeCompleted = "node.completed",
  NodeFailed = "node.failed",
  NodeCancelled = "node.cancelled",

  // input (includes human_action via pending_kind field)
  InputRequested = "input.requested",
  InputSubmitted = "input.submitted",

  // data edit
  DataEditRequested = "data_edit.requested",

  // node
  ConditionEvaluated = "condition.evaluated",
  LoopIterationCompleted = "loop.iteration.completed",
  NodeApprovalFlowResult = "node.approval_flow.result",

  // child_run
  ChildRunStarted = "child_run.started",
  ChildRunCompleted = "child_run.completed",
  ChildRunFailed = "child_run.failed",
  ChildRunCancelled = "child_run.cancelled",

  // rating (SA-2741): a run was rated somewhere (UI / API / bot) → refresh chips.
  RatingUpdated = "rating.updated",

  // context
  ContextUpdated = "context.updated",

  // edge
  EdgeTraversed = "edge.traversed",

  // artifact
  ArtifactAdded = "artifact.added",

  // node_input
  NodeInputEdited = "node_input.edited",

  // worker app health (SA-2047 R14 feed → worker-app identity view; supersedes
  // the retired R9/R10 "worker.replica.health" event on the same feed/room —
  // the control-plane's worker_health_feed_usecase now emits only this one)
  WorkerAppHealth = "worker.app.health",
}

export enum SocketRoom {
  Run = "run:{run_id}",
  Workflow = "workflow:{workflow_id}",
  WorkersHealth = "workers:health",
}

export enum SocketRoomEvent {
  JoinRun = "join:run",
  LeaveRun = "leave:run",
  JoinWorkflow = "join:workflow",
  LeaveWorkflow = "leave:workflow",
}

export interface ISocketEventMeta {
  _event: string;
  _timestamp: string;
  event_id: string;
  timestamp: string;
  source: string;
}

export interface SocketMessageEnvelope {
  [key: string]: unknown;
  status?: string;
  data?: unknown;
  chunk_index?: number;
}

export type SocketDataChunkPayload = Partial<BaseSocketEvent> & {
  data?: unknown;
  event_type?: string;
  eventType?: string;
  event?: string;
  state_updates?: Record<string, unknown>;
  output_port?: string;
  taken_edge_ports?: Record<string, unknown>;
  [key: string]: unknown;
};

interface IRunContext {
  run_id: string;
  workflow_id: string;
}

interface ITaskContext extends IRunContext {
  task_id: string;
  node_id: string;
}

export interface IInputSchemaField {
  key: string;
  type: string;
  label: string;
}

interface IInputSchema {
  input_schema: IField[];
  instruction: string;
}

export interface IRunStartedPayload extends IRunContext {
  _id?: string;
  error?: string;
}

export interface IRunCompletedPayload extends IRunContext {
  status: string;
  error?: string;
  correlation_id: string;
  callback_topic: string;
  output_data: Record<string, unknown>;
}

export interface IRunFailedPayload extends IRunContext {
  error: string;
  failed_task_id: string;
  correlation_id: string;
  callback_topic: string;
}

export interface IRunCancelledPayload extends IRunContext {
  cancelled_by: string;
  reason: string;
}

export interface IRunPausedPayload extends IRunContext {
  _id?: string;
}

export interface IRunResumedPayload extends IRunContext {
  _id?: string;
}

export interface ITaskDispatchedPayload extends ITaskContext {
  worker_type: string;
  input: Record<string, unknown>;
  node_config: Record<string, unknown>;
  idempotency_key: string;
  correlation_id: string;
}

export interface ITaskCompletedPayload extends ITaskContext {
  status: string;
  execution_time_ms: number;
}

export interface ITaskFailedPayload extends ITaskContext {
  error_code: string;
  error_message: string;
  attempt: number;
}

export interface ITaskCancelledPayload extends ITaskContext {
  cancelled_by: string;
  reason: string;
}

export interface ITaskRetryingPayload extends ITaskContext {
  attempt: number;
  next_retry_at: string;
}

export interface ITaskResumedPayload extends ITaskContext {
  _id?: string;
}

export interface ITaskSkippedPayload extends ITaskContext {
  reason: string;
}

export interface ITaskRerunPayload extends ITaskContext {
  previous_status: string;
}


export interface IInputSubmittedPayload extends ITaskContext {
  _id?: string;
}

export interface IDataEditRequestedPayload extends ITaskContext, IInputSchema {
  root_run_id: string;
  depth: number;
  parent_run_id: string;
  source_file_id: string;
  source_node_id: string;
  source_node_name: string;
  _is_final: boolean;
}

export interface IHumanActionRequestedPayload
  extends ITaskContext,
  IInputSchema {
  confirm_label: string | null;
  reject_label: string | null;
}

export interface IHumanActionCompletedPayload extends ITaskContext {
  action: "confirm" | "reject";
}

export interface IConditionEvaluatedPayload extends IRunContext {
  node_id: string;
  expression: string;
  result: boolean;
  branch: "then" | "else";
}

export interface ILoopIterationCompletedPayload extends IRunContext {
  node_id: string;
  iteration: number;
  total: number;
}

export interface IChildRunStartedPayload {
  parent_run_id: string;
  parent_node_id: string;
  child_run_id: string;
  child_workflow_id: string;
  root_run_id: string;
  depth: number;
  _is_final: boolean;
}

export interface IChildRunCompletedPayload extends IChildRunStartedPayload {
  status: string;
}

export interface IContextUpdatedPayload extends IRunContext {
  node_id: string;
  node_name: string;
  data_type: "inline" | "file_ref";
}

export interface IEdgeTraversedPayload extends IRunContext {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
  source_port_id: string;
}

export interface IArtifactAddedPayload extends IRunContext {
  node_id: string;
  file_id: string;
  name: string;
  content_type: string;
  artifact_type: string;
}

export interface INodeInputEditedPayload extends IRunContext {
  node_id: string;
  task_id: string;
  previous_file_id: string;
  new_file_id: string;
}


export interface SocketEventPayloadMap {
  [SocketEvent.RunStatus]: IRunStartedPayload | IRunCompletedPayload | IRunFailedPayload;
  [SocketEvent.NodeDispatched]: ITaskDispatchedPayload;
  [SocketEvent.NodeCompleted]: ITaskCompletedPayload;
  [SocketEvent.NodeFailed]: ITaskFailedPayload;
  [SocketEvent.NodeCancelled]: ITaskCancelledPayload;

  [SocketEvent.InputRequested]: IInputRequestedPayload;
  [SocketEvent.InputSubmitted]: IInputSubmittedPayload;

  [SocketEvent.DataEditRequested]: IDataEditRequestedPayload;

  [SocketEvent.ConditionEvaluated]: IConditionEvaluatedPayload;
  [SocketEvent.LoopIterationCompleted]: ILoopIterationCompletedPayload;

  [SocketEvent.ChildRunStarted]: IChildRunStartedPayload;
  [SocketEvent.ChildRunCompleted]: IChildRunCompletedPayload;
  [SocketEvent.ChildRunFailed]: IChildRunCompletedPayload;
  [SocketEvent.ChildRunCancelled]: IChildRunCompletedPayload;

  [SocketEvent.ContextUpdated]: IContextUpdatedPayload;
  [SocketEvent.EdgeTraversed]: IEdgeTraversedPayload;
  [SocketEvent.ArtifactAdded]: IArtifactAddedPayload;
  [SocketEvent.NodeInputEdited]: INodeInputEditedPayload;
  [SocketEvent.NodeApprovalFlowResult]: IApprovalFlowPayload;
  [SocketEvent.WorkerAppHealth]: IWorkerAppHealthPayload;

}

export type SocketEventFullPayload<E extends SocketEvent> =
  SocketEventPayloadMap[E] & ISocketEventMeta;

export interface IJoinRunPayload {
  run_id: string;
}

export interface IJoinWorkflowPayload {
  workflow_id: string;
}