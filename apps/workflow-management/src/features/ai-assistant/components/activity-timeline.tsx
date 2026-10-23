// =============================================================================
// SA-2218 — Agent Activity Timeline (expandable "what the agent is thinking/doing")
// =============================================================================
// Renders the ordered steps of a design-stream turn (planning → tool calls →
// plan/review → …) as a collapsible dropdown, the way a mainstream AI chat shows
// "thought for… / used tool…". Collapsed by default: one summary line (the newest
// step while running, or "N steps" once done). Mirrors the chat-sdk tool-group
// precedent and reuses the @ldc/ui collapsible primitive.
// =============================================================================

import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from "@ldc/ui/components/collapsible";
import { ChevronDownIcon, LoaderIcon } from "lucide-react";
import { useState } from "react";
import type { FC } from "react";
import type { AgentStepStatus, IAgentStep } from "../types";

const STATUS_DOT: Record<AgentStepStatus, string> = {
    running: "bg-primary animate-pulse",
    ok: "bg-emerald-500",
    warn: "bg-amber-500",
    error: "bg-destructive",
};

const dotClass = (status?: AgentStepStatus) =>
    STATUS_DOT[status ?? "ok"] ?? STATUS_DOT.ok;

interface ActivityTimelineProps {
    steps: IAgentStep[];
    /** True while the turn is still streaming — shows a spinner + the live label. */
    running: boolean;
    className?: string;
}

export const ActivityTimeline: FC<ActivityTimelineProps> = ({
    steps,
    running,
    className,
}) => {
    const { t } = useLanguage();
    const [open, setOpen] = useState(false);

    if (!steps.length) return null;

    const latest = steps[steps.length - 1];
    const summary = running
        ? (latest?.label ?? t("ai_assistant_thinking", "Thinking..."))
        : t("ai_activity_summary", "{{count}} steps", { count: steps.length });

    return (
        <Collapsible
            open={open}
            onOpenChange={setOpen}
            className={cn(
                "aui-activity-timeline w-full rounded-2xl border border-border/50 bg-muted/30 px-3 py-2",
                className,
            )}
        >
            <CollapsibleTrigger
                className={cn(
                    "group/activity flex w-full items-center gap-2 text-left text-xs font-medium",
                    "text-muted-foreground transition-colors hover:text-foreground",
                )}
            >
                {running ? (
                    <LoaderIcon className="size-3.5 shrink-0 animate-spin" />
                ) : (
                    <span className="flex size-3.5 shrink-0 items-center justify-center">
                        <span className="size-1.5 rounded-full bg-muted-foreground/60" />
                    </span>
                )}
                <span className="grow truncate">{summary}</span>
                <ChevronDownIcon
                    className={cn(
                        "size-3.5 shrink-0 transition-transform duration-150",
                        "group-data-[state=open]/activity:rotate-180",
                    )}
                />
            </CollapsibleTrigger>

            <CollapsibleContent className="overflow-hidden">
                <ol className="mt-2 flex flex-col gap-2 border-t border-border/40 pt-2">
                    {steps.map((step) => (
                        <li key={step.id} className="flex gap-2">
                            <span
                                className={cn(
                                    "mt-1.5 size-1.5 shrink-0 rounded-full",
                                    dotClass(step.status),
                                )}
                                aria-hidden
                            />
                            <div className="min-w-0 flex-1">
                                <div className="wrap-break-word text-xs text-foreground">
                                    {step.label}
                                </div>
                                {step.detail && (
                                    <div className="wrap-break-word mt-0.5 whitespace-pre-wrap text-[11px] leading-relaxed text-muted-foreground">
                                        {step.detail}
                                    </div>
                                )}
                            </div>
                        </li>
                    ))}
                </ol>
            </CollapsibleContent>
        </Collapsible>
    );
};
