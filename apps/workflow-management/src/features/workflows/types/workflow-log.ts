export type ExecutionStatus = "PROCESSING" | "SUCCESS" | "ERROR" | "TIMEOUT";

export type LightLogAction = "NODE_ADDED" | "CONNECTION_ADDED" | "WORKFLOW_RENAMED";
export type HeavyLogAction = "NODE_EXECUTED" | "WORKFLOW_EXECUTED" | "WORKFLOW_SAVED";
export type LogAction = LightLogAction | HeavyLogAction;

export interface IWorkflowLogs {
    id: string;
    workflowId: string;
    timestamp: number;
    actionName: LogAction;
    nodeId?: string;
    nodeName?: string;
    status?: ExecutionStatus;
    tokensUsed?: number;
    errorMessage?: string;
}

export interface INodeLogData {
  nodeId: string;
  nodeName?: string;
  timestamp: number;
  errorMessage?: string;
}

export interface IWorkflowLogData {
  timestamp: number;
  errorMessage?: string;
}