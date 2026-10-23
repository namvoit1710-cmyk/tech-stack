import { ExecutionStatus, LogAction } from "../features/workflows/types/workflow-log";

export const LOG_ACTION = {
    NODE_ADDED: "NODE_ADDED",
    CONNECTION_ADDED: "CONNECTION_ADDED",
    WORKFLOW_RENAMED: "WORKFLOW_RENAMED",
    NODE_EXECUTED: "NODE_EXECUTED",
    WORKFLOW_EXECUTED: "WORKFLOW_EXECUTED",
    WORKFLOW_SAVED: "WORKFLOW_SAVED",
} as const satisfies Record<LogAction, LogAction>;

export const EXECUTION_STATUS = {
    PROCESSING: "PROCESSING",
    SUCCESS: "SUCCESS",
    ERROR: "ERROR",
    TIMEOUT: "TIMEOUT",
} as const satisfies Record<ExecutionStatus, ExecutionStatus>;

export const LOG_ACTION_ICON: Record<LogAction, string> = {
    NODE_ADDED: "plus",
    CONNECTION_ADDED: "waypoints",
    WORKFLOW_RENAMED: "pencil",
    NODE_EXECUTED: "play",
    WORKFLOW_EXECUTED: "tv-minimal-play",
    WORKFLOW_SAVED: "save",
};
