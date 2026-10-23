
// SA-2218: one entry in the assistant turn's activity timeline — a single thing the
// designer agent was thinking or doing, derived from a design-stream event. Ordered,
// append-only per turn; rendered as an expandable dropdown (see activity-timeline.tsx).
export type AgentStepKind =
    | "planning"
    | "thinking"
    | "tool_call"
    | "tool_result"
    | "plan"
    | "critique"
    | "review"
    | "repair";

export type AgentStepStatus = "running" | "ok" | "warn" | "error";

export interface IAgentStep {
    id: string;
    kind: AgentStepKind;
    /** Short one-line label shown collapsed and as the row title. */
    label: string;
    /** Longer text revealed under the row (rationale, critique, issues, error, narration). */
    detail?: string;
    status?: AgentStepStatus;
    at: number;
}

export interface IAIAssistantMessage {
    id: string;
    role: "user" | "assistant";
    content: string;
    workflowId?: string;
    isThinking?: boolean;
    // SA-1582: drives claymorphism accent styling for non-success turns.
    variant?: "clarify" | "error";
    // SA-2218: the ordered activity timeline for this assistant turn (planning →
    // tool calls → plan/review → …). Travels to the renderer via the assistant-context
    // side-channel (getMessageById), so convertMessage stays content-only.
    steps?: IAgentStep[];
    createdAt: number;
}

// SA-1582: the Workflow Designer Agent (SA-1437) surfaces one of these on every
// /api/v1/execute turn. Kept as string constants to avoid magic strings at call sites.
export const WORKFLOW_DESIGN_STATUS = {
    SUCCESS: "success",
    NEEDS_CLARIFICATION: "needs_clarification",
    ERROR: "error",
} as const;

export type WorkflowDesignStatus =
    (typeof WORKFLOW_DESIGN_STATUS)[keyof typeof WORKFLOW_DESIGN_STATUS];

// Request body for the direct designer-agent call (matches the agent-sdk
// ExecuteAgentInput contract: message + conv_id + identity + source).
export interface IWorkflowDesignerExecutePayload {
    message: string;
    conv_id: string;
    user_id?: string;
    tenant_id?: string;
    source?: string;
}

// agent_data emitted by the designer agent's format_response node.
export interface IWorkflowDesignerAgentData {
    workflow?: Record<string, unknown>;
    workflow_id?: string;
    run_id?: string;
    link?: string;
    raw_result?: Record<string, unknown>;
}

// ExecuteAgentOutput as returned by the designer agent: the human-facing text
// rides in `message`, the routing decision in `status`, the artifact in agent_data.
export interface IWorkflowDesignerExecuteResponse {
    message: string;
    status: string;
    session_id?: string;
    agent_data?: IWorkflowDesignerAgentData;
    error?: string | null;
    error_code?: string | null;
}

/**
 * A single Server-Sent Event from `GET /api/v1/design/stream`. Field set grounded
 * (SA-2218) against the agent's real emitters (`tool_loop_designer.py`,
 * `validate_repair_gate.py`, `design_workflow_use_case.py`):
 *  - `planning`  {message}                         — flow started
 *  - `thinking`  {delta}                           — LLM narration tokens (accumulate)
 *  - `tool_call` {tool, args_summary}              — the agent is DOING something
 *  - `tool_result` {tool, summary}                 — that action's outcome
 *  - `plan`      {nodes[], rationale}              — finalized node plan
 *  - `critique`  {critique}                        — the plan's self-critique
 *  - `review`    {ok, issues[]}                    — payload self-review verdict
 *  - `repair_iteration` {attempt, error_code, error, blocked}
 *  - `content_delta` {delta}                       — final answer, streamed word-by-word
 *  - `done`      {conv_id, status, content, workflow_id, run_id, link}  — terminal
 *  - `error`     {message}                          — terminal error
 */
export interface IDesignStreamEvent {
    event: string;
    conv_id?: string;
    status?: string;
    content?: string;
    /** Incremental token for `thinking` and `content_delta` events. */
    delta?: string;
    message?: string;
    workflow_id?: string;
    run_id?: string;
    link?: string;
    // tool_call / tool_result
    tool?: string;
    args_summary?: string;
    summary?: string;
    // plan (nodes is an array here) / legacy count carriers
    nodes?: number | unknown[];
    edges?: number;
    rationale?: string;
    // critique / review
    critique?: string;
    ok?: boolean;
    issues?: string[];
    // repair_iteration
    attempt?: number;
    error_code?: string;
    error?: string;
    blocked?: boolean;
    // misc / legacy
    name?: string;
    reason?: string;
    code?: string;
}

export interface IApiErrorBody {
    message?: string;
    error?: string;
    statusCode?: number;
}
