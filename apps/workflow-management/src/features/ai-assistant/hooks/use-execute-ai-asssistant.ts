
import { useAuth } from "@/components/providers/auth-provider";
import { env } from "@/env";
import { useLanguage } from "@/hooks/use-language";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { useCallback, useRef, useState } from "react";
import { WORKFLOW_DESIGN_STATUS } from "../types";
import type {
    AgentStepStatus,
    IAgentStep,
    IAIAssistantMessage,
    IDesignStreamEvent,
} from "../types";
import { streamWorkflowDesign } from "./apis/design-stream";

// SA-1582: identifies this caller to the agent (ExecuteAgentInput.source).
const CHAT_SOURCE = "workflow-management-create-with-ai";

interface UseExecuteAIAssistantOptions {
    // Client-minted conversation id, fresh per page load (see WorkflowsPage).
    convId: string;
    // Invoked once the agent reports a created workflow, so the page can open it
    // on the canvas.
    onWorkflowCreated?: (workflowId: string) => void;
}

interface UseExecuteAIAssistantReturn {
    messages: IAIAssistantMessage[];
    isRunning: boolean;
    sendMessage: (text: string) => Promise<void>;
}

type Translate = ReturnType<typeof useLanguage>["t"];

/** A step drafted from an event, before it gets an id + timestamp. */
type StepDraft = Pick<IAgentStep, "kind" | "label" | "status"> &
    Partial<Pick<IAgentStep, "detail">>;

/** Normalize an optional string: blank/whitespace → undefined (so `??` can fall through). */
const nz = (s?: string): string | undefined => {
    const v = s?.trim();
    if (!v) return undefined;
    return v;
};

/**
 * SA-2218: map ONE design-stream event to a timeline step — grounded on the agent's
 * real emitters (tool_loop_designer.py etc.). `thinking` / `content_delta` (token
 * streams) and the `done` / `error` terminals are handled by the caller, not here.
 * Returns null for events that don't become their own step.
 * Exported for unit testing (SA-2218) — the event vocabulary is the load-bearing part.
 */
export const stepFromEvent = (event: IDesignStreamEvent, t: Translate): StepDraft | null => {
    switch (event.event) {
        case "planning":
            return {
                kind: "planning",
                label: nz(event.message) ?? t("ai_progress_planning", "Understanding your request…"),
                status: "running",
            };
        case "tool_call":
            // args_summary is already a human sentence ("creating 'Forecast'", "listing
            // available node types", …) — this is the "what the agent is doing" line.
            return {
                kind: "tool_call",
                label: nz(event.args_summary) ?? nz(event.tool) ?? t("ai_progress_working", "Working…"),
                status: "running",
            };
        case "tool_result":
            return {
                kind: "tool_result",
                label: nz(event.summary) ?? nz(event.tool) ?? t("ai_progress_step_done", "Done"),
                status: "ok",
            };
        case "plan": {
            const count = Array.isArray(event.nodes)
                ? event.nodes.length
                : (event.nodes ?? 0);
            return {
                kind: "plan",
                label: t("ai_progress_plan", "Planned {{count}} node(s)", { count }),
                detail: nz(event.rationale),
                status: "ok",
            };
        }
        case "critique":
            return {
                kind: "critique",
                label: t("ai_progress_critique", "Reviewing the plan…"),
                detail: nz(event.critique),
                status: "running",
            };
        case "review":
            return {
                kind: "review",
                label: event.ok
                    ? t("ai_progress_review_ok", "Self-review passed")
                    : t("ai_progress_review_issues", "Self-review found issues"),
                detail: event.issues?.length ? event.issues.join("; ") : undefined,
                status: event.ok ? "ok" : "warn",
            };
        case "repair_iteration":
            return {
                kind: "repair",
                label: t("ai_progress_repair", "Fixing an issue (attempt {{count}})…", {
                    count: event.attempt ?? 1,
                }),
                detail: nz(event.error) ?? nz(event.error_code),
                status: event.blocked ? "error" : "running",
            };
        default:
            return null;
    }
};

export const useExecuteAIAssistant = ({
    convId,
    onWorkflowCreated,
}: UseExecuteAIAssistantOptions): UseExecuteAIAssistantReturn => {
    const { t } = useLanguage();
    const { userId } = useAuth();
    const [messages, setMessages] = useState<IAIAssistantMessage[]>([]);
    const [isRunning, setIsRunning] = useState(false);

    const isRunningRef = useRef(false);

    const appendMessage = useCallback((message: IAIAssistantMessage) => {
        setMessages((prev) => [...prev, message]);
    }, []);

    const removeMessage = useCallback((messageId: string) => {
        setMessages((prev) => prev.filter((msg) => msg.id !== messageId));
    }, []);

    const updateMessage = useCallback(
        (messageId: string, patch: Partial<IAIAssistantMessage>) => {
            setMessages((prev) =>
                prev.map((msg) =>
                    msg.id === messageId ? { ...msg, ...patch } : msg,
                ),
            );
        },
        [],
    );

    const sendMessage = useCallback(
        async (text: string) => {
            if (!text.trim() || isRunningRef.current) return;

            const userMessage: IAIAssistantMessage = {
                id: `user_${Date.now()}`,
                role: "user",
                content: text,
                createdAt: Date.now(),
            };
            appendMessage(userMessage);

            const thinkingMessageId = `thinking_${Date.now()}`;
            appendMessage({
                id: thinkingMessageId,
                role: "assistant",
                content: "",
                isThinking: true,
                steps: [],
                createdAt: Date.now(),
            });

            setIsRunning(true);
            isRunningRef.current = true;

            // Streaming accumulators (declared here so `catch` can clean up).
            // SA-2218: `steps` is the ordered activity timeline for this turn — every
            // event APPENDS to it (never overwrites), and it is mirrored onto whichever
            // message is currently live (the thinking bubble, then the answer).
            const steps: IAgentStep[] = [];
            let stepSeq = 0;
            // The id of the currently-open `thinking` narration step, so token deltas
            // extend it in place; reset whenever any other step intervenes (so the
            // timeline reads think → do → think → do rather than one giant block).
            let thinkingStepId: string | null = null;
            let thinkingText = "";
            let answerId: string | null = null;
            let answerText = "";

            // Mirror the current `steps` onto the live message. While the answer hasn't
            // started, the collapsed thinking bubble shows the newest step's label.
            // Mark every still-open ("running") step as done, so only the newest
            // active step keeps its pulsing dot (a step is finished once the next begins).
            const finalizeRunning = (status: AgentStepStatus = "ok") => {
                for (const s of steps) if (s.status === "running") s.status = status;
            };

            const syncSteps = () => {
                const snapshot = steps.map((s) => ({ ...s }));
                if (answerId !== null) {
                    updateMessage(answerId, { steps: snapshot });
                } else {
                    const latest = steps[steps.length - 1];
                    updateMessage(thinkingMessageId, {
                        steps: snapshot,
                        content: latest?.label ?? "",
                    });
                }
            };

            const pushStep = (draft: StepDraft) => {
                finalizeRunning(); // the previous step is done once a new one starts
                thinkingStepId = null; // a non-thinking step closes the open narration
                steps.push({ id: `step_${stepSeq++}`, at: Date.now(), ...draft });
                syncSteps();
            };

            try {
                // Same identity SA-1582 sent to /execute; the SSE endpoint threads
                // the turn by conv_id (its own history store) and ignores the rest.
                const resolvedUserId = userId ?? env.PUBLIC_DEV_USER_ID;
                const resolvedTenantId = env.PUBLIC_DEV_TENANT_ID;

                const result = await streamWorkflowDesign({
                    message: text,
                    convId,
                    source: CHAT_SOURCE,
                    ...(resolvedUserId ? { userId: resolvedUserId } : {}),
                    ...(resolvedTenantId ? { tenantId: resolvedTenantId } : {}),
                    onEvent: (event) => {
                        // LLM 'thinking' narration — accumulate into a single running
                        // step so it reads as one "Thinking…" entry, not one per token.
                        if (event.event === "thinking") {
                            thinkingText += event.delta ?? "";
                            if (thinkingStepId === null) {
                                finalizeRunning(); // close the prior step before a new narration
                                thinkingStepId = `step_${stepSeq++}`;
                                steps.push({
                                    id: thinkingStepId,
                                    kind: "thinking",
                                    label: t("ai_assistant_thinking", "Thinking..."),
                                    detail: "",
                                    status: "running",
                                    at: Date.now(),
                                });
                            }
                            const s = steps.find((x) => x.id === thinkingStepId);
                            if (s) s.detail = thinkingText.trim();
                            syncSteps();
                            return;
                        }
                        // Final answer streaming word-by-word: turn the thinking bubble
                        // into a normal assistant message, carrying the timeline across.
                        if (event.event === "content_delta") {
                            if (answerId === null) {
                                finalizeRunning();
                                removeMessage(thinkingMessageId);
                                answerId = `assistant_${Date.now()}`;
                                appendMessage({
                                    id: answerId,
                                    role: "assistant",
                                    content: "",
                                    steps: steps.map((s) => ({ ...s })),
                                    createdAt: Date.now(),
                                });
                            }
                            answerText += event.delta ?? "";
                            updateMessage(answerId, { content: answerText });
                            return;
                        }
                        if (event.event === "done" || event.event === "error") return;
                        // Every other event becomes an ordered step — no `thinkingText`
                        // gate (SA-2218): tool_call / tool_result / plan / review / repair
                        // keep flowing even after `thinking` narration has started.
                        const draft = stepFromEvent(event, t);
                        if (draft) pushStep(draft);
                    },
                });

                const status = result.status;
                const workflowId = result.workflow_id;
                const isSuccess = status === WORKFLOW_DESIGN_STATUS.SUCCESS;
                const isError = status === WORKFLOW_DESIGN_STATUS.ERROR;
                const variant = isError ? "error" : isSuccess ? undefined : "clarify";

                finalizeRunning(isError ? "error" : "ok");
                const finalSteps = steps.map((s) => ({ ...s }));

                const fallbackContent = isSuccess
                    ? t("notification.workflow_created_successfully_with_ai")
                    : t("notification.workflow_creation_failed");
                const finalContent =
                    nz(answerText) ?? nz(result.content) ?? fallbackContent;

                if (answerId !== null) {
                    // Finalize the streamed answer in place (add link/variant/steps).
                    updateMessage(answerId, {
                        content: finalContent,
                        workflowId: isSuccess ? workflowId : undefined,
                        variant,
                        steps: finalSteps,
                    });
                } else {
                    // No deltas streamed (e.g. older backend) — append the answer,
                    // keeping the activity timeline attached so it stays expandable.
                    removeMessage(thinkingMessageId);
                    appendMessage({
                        id: `assistant_${Date.now()}`,
                        role: "assistant",
                        content: finalContent,
                        workflowId: isSuccess ? workflowId : undefined,
                        variant,
                        steps: finalSteps,
                        createdAt: Date.now(),
                    });
                }

                if (isSuccess && workflowId) {
                    toast.success(
                        t("notification.success"),
                        t("notification.workflow_created_successfully_with_ai"),
                    );
                    onWorkflowCreated?.(workflowId);
                }
            } catch (error) {
                removeMessage(thinkingMessageId);
                if (answerId !== null) removeMessage(answerId);

                const errMsg =
                    error instanceof Error
                        ? error.message
                        : t("notification.workflow_creation_failed");

                finalizeRunning("error");
                appendMessage({
                    id: `error_${Date.now()}`,
                    role: "assistant",
                    content: `${t("notification.workflow_creation_failed")}: ${errMsg}`,
                    variant: "error",
                    // Keep whatever steps ran before the failure so the user can see
                    // how far the agent got.
                    steps: steps.length ? steps.map((s) => ({ ...s })) : undefined,
                    createdAt: Date.now(),
                });

                toast.error(t("notification.error"), errMsg);
            } finally {
                setIsRunning(false);
                isRunningRef.current = false;
            }
        },
        [convId, userId, appendMessage, removeMessage, updateMessage, t, onWorkflowCreated],
    );

    return { messages, isRunning, sendMessage };
};
