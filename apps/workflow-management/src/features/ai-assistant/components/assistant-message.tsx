import { useLanguage } from "@/hooks/use-language";
import {
    MessagePrimitive,
    useAuiState
} from "@assistant-ui/react";
import { cn } from "@ldc/ui";
import type { FC } from "react";
import { Link } from "react-router-dom";
import { useAIAssistantContext } from "../context/assistant-context";
import { ActivityTimeline } from "./activity-timeline";
import { MarkdownText } from "./markdown-text";

// SA-1582: claymorphism bubble tints per turn variant (clarify / error / default).
const CLAY_SHADOW =
    "shadow-[0_6px_18px_-8px_rgba(15,23,42,0.25),inset_0_1px_1px_rgba(255,255,255,0.6)]";

const VARIANT_STYLES = {
    clarify: "border-amber-200/70 bg-amber-50/70",
    error: "border-destructive/30 bg-destructive/5",
    default: "border-border/50 bg-muted/40",
} as const;

export const AssistantMessage: FC = () => {
    const { t } = useLanguage();
    const messageId = useAuiState((s) => s.message.id);
    const { getMessageById } = useAIAssistantContext();

    const internalMessage = getMessageById(messageId);
    const workflowId = internalMessage?.workflowId;
    const isThinking = internalMessage?.isThinking;
    const variant = internalMessage?.variant ?? "default";
    // SA-2218: the agent's activity timeline for this turn (may be empty).
    const steps = internalMessage?.steps ?? [];

    if (isThinking) {
        return (
            <MessagePrimitive.Root
                className="aui-assistant-message-root fade-in slide-in-from-bottom-1 relative mx-auto w-full max-w-(--thread-max-width) animate-in py-3 duration-150"
                data-role="assistant"
            >
                <div className="aui-assistant-message-content flex flex-col gap-2 px-2 text-muted-foreground">
                    <div className="flex items-center gap-2">
                        <div className="flex gap-1">
                            <span className="inline-block size-2 rounded-full bg-current animate-bounce [animation-delay:-0.3s]" />
                            <span className="inline-block size-2 rounded-full bg-current animate-bounce [animation-delay:-0.15s]" />
                            <span className="inline-block size-2 rounded-full bg-current animate-bounce" />
                        </div>
                        {/* Before any step arrives, show the generic label. Once the
                            timeline exists it owns the live label — no duplication. */}
                        {steps.length === 0 && (
                            <span className="text-sm">
                                {t("ai_assistant_thinking") || "Thinking..."}
                            </span>
                        )}
                    </div>
                    {/* SA-2218: expandable timeline of what the agent is doing, live. */}
                    {steps.length > 0 && (
                        <ActivityTimeline steps={steps} running />
                    )}
                </div>
            </MessagePrimitive.Root>
        );
    }

    return (
        <MessagePrimitive.Root
            className="aui-assistant-message-root fade-in slide-in-from-bottom-1 relative mx-auto w-full max-w-(--thread-max-width) animate-in py-3 duration-150"
            data-role="assistant"
        >
            <div
                className={cn(
                    "aui-assistant-message-content wrap-break-word max-w-[90%] rounded-3xl rounded-bl-lg border px-4 py-3 leading-relaxed text-foreground",
                    CLAY_SHADOW,
                    VARIANT_STYLES[variant],
                )}
            >
                {/* SA-2218: the agent's activity timeline persists after the answer
                    lands — collapsed, expandable to the full think/do transcript. */}
                {steps.length > 0 && (
                    <ActivityTimeline
                        steps={steps}
                        running={false}
                        className="mb-3"
                    />
                )}

                <MessagePrimitive.Parts>
                    {({ part }) => {
                        if (part.type === "text") return <MarkdownText />;
                        return null;
                    }}
                </MessagePrimitive.Parts>

                {workflowId && (
                    <div className="mt-3 pt-3 border-t border-border/50">
                        <Link
                            to={`/workflow/${workflowId}`}
                            className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline transition-colors"
                        >
                            {t("view_workflow") || "View Workflow"} →
                        </Link>
                    </div>
                )}
            </div>

            {/* <div className="aui-assistant-message-footer mt-1 ml-2 flex min-h-6 items-center">
                <AssistantActionBar />
            </div> */}
        </MessagePrimitive.Root>
    );
};

// const AssistantActionBar: FC = () => {
//     return (
//         <ActionBarPrimitive.Root
//             hideWhenRunning
//             autohide="not-last"
//             className="aui-assistant-action-bar-root -ml-1 flex gap-1 text-muted-foreground"
//         >
//             <ActionBarPrimitive.Copy asChild>
//                 <TooltipIconButton tooltip="Copy">
//                     <MessagePrimitive.If copied>
//                         <CheckIcon />
//                     </MessagePrimitive.If>
//                     <MessagePrimitive.If copied={false}>
//                         <CopyIcon />
//                     </MessagePrimitive.If>
//                 </TooltipIconButton>
//             </ActionBarPrimitive.Copy>
//             <ActionBarPrimitive.Reload asChild>
//                 <TooltipIconButton tooltip="Retry">
//                     <RefreshCwIcon />
//                 </TooltipIconButton>
//             </ActionBarPrimitive.Reload>
//         </ActionBarPrimitive.Root>
//     );
// };