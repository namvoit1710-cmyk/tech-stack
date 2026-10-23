export enum WORKFLOW_TAB_ENUM {
    WORKFLOWS = "workflows",
    EXECUTION_HISTORY = "execution-history",
    FAVORITES = "favorites",
    NODE_DEFINITIONS = "node-definitions"
}

export enum WORKFLOW_EXECUTION_STATUS_ENUM {
    CREATED = "created",
    PENDING = "pending",
    RUNNING = "running",
    COMPLETED = "completed",
    FAILED = "failed",
    CANCELLED = "cancelled",
    PAUSED = "paused",
}

export const WORKFLOW_EXECUTION_STATUS_MAP = {
    [WORKFLOW_EXECUTION_STATUS_ENUM.CREATED]: "Information",
    [WORKFLOW_EXECUTION_STATUS_ENUM.PENDING]: "Information",
    [WORKFLOW_EXECUTION_STATUS_ENUM.RUNNING]: "Critical",
    [WORKFLOW_EXECUTION_STATUS_ENUM.COMPLETED]: "Positive",
    [WORKFLOW_EXECUTION_STATUS_ENUM.FAILED]: "Negative",
    [WORKFLOW_EXECUTION_STATUS_ENUM.CANCELLED]: "Neutral",
    [WORKFLOW_EXECUTION_STATUS_ENUM.PAUSED]: "Information",
}

export const WORKFLOW_EXECUTION_STATUS_TEXT: Record<string, string> = {
    [WORKFLOW_EXECUTION_STATUS_ENUM.CREATED]: "status_value.created",
    [WORKFLOW_EXECUTION_STATUS_ENUM.PENDING]: "status_value.pending",
    [WORKFLOW_EXECUTION_STATUS_ENUM.RUNNING]: "status_value.running",
    [WORKFLOW_EXECUTION_STATUS_ENUM.COMPLETED]: "status_value.completed",
    [WORKFLOW_EXECUTION_STATUS_ENUM.FAILED]: "status_value.failed",
    [WORKFLOW_EXECUTION_STATUS_ENUM.CANCELLED]: "status_value.cancelled",
    [WORKFLOW_EXECUTION_STATUS_ENUM.PAUSED]: "status_value.paused",
}

export const WORKFLOW_EXECUTION_STATUS_OPTIONS = [
    { value: WORKFLOW_EXECUTION_STATUS_ENUM.CREATED, label: "Created" },
    { value: WORKFLOW_EXECUTION_STATUS_ENUM.PENDING, label: "Pending" },
    { value: WORKFLOW_EXECUTION_STATUS_ENUM.RUNNING, label: "Running" },
    { value: WORKFLOW_EXECUTION_STATUS_ENUM.COMPLETED, label: "Completed" },
    { value: WORKFLOW_EXECUTION_STATUS_ENUM.FAILED, label: "Failed" },
    { value: WORKFLOW_EXECUTION_STATUS_ENUM.CANCELLED, label: "Cancelled" },
    { value: WORKFLOW_EXECUTION_STATUS_ENUM.PAUSED, label: "Paused" },
]
