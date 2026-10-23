import { cn } from "@ldc/ui";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { Star } from "lucide-react";
import { useState } from "react";
import { useLanguage } from "@/hooks/use-language";
import { useRunRaters, useRunRatersByRun } from "../../hooks/apis/workflows";

// SA-2741 / SA-2876: read-only star-total chip (★ n). Two contexts:
//   - pass `workflowId` → the workflow's total stars over all its runs; hover =
//     who starred any of its runs (Workflows tab + Execution-History rows).
//   - pass `runId` → THIS run's own stars; hover = who starred that run.
// Hover names are NAMES ONLY (privacy), lazily fetched on open.
export interface RatingChipProps {
    workflowId?: string | null;
    runId?: string | null;
    count?: number;
    className?: string;
}

export const RatingChip = ({ workflowId, runId, count = 0, className }: RatingChipProps) => {
    const { t } = useLanguage();
    const [open, setOpen] = useState(false);
    const byRun = !!runId;
    const wfRaters = useRunRaters(workflowId ?? "", { enabled: open && !byRun && !!workflowId });
    const runRaters = useRunRatersByRun(runId ?? "", { enabled: open && byRun });
    const raters = byRun ? runRaters.data : wfRaters.data;
    const hasSource = byRun ? !!runId : !!workflowId;

    const rated = count > 0;
    const chip = (
        <span
            className={cn(
                "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
                rated
                    ? "border-amber-500/30 bg-amber-500/15 text-amber-700 dark:text-amber-400"
                    : "border-border bg-muted text-muted-foreground",
                className,
            )}
        >
            <Star className={cn("size-3", rated ? "fill-amber-500 text-amber-500" : "text-muted-foreground/50")} />
            {rated ? <b className="font-semibold tabular-nums">{count}</b> : <span>{t("rating_not_rated_yet")}</span>}
        </span>
    );

    if (!rated || !hasSource) return chip;

    const extra = raters ? Math.max(0, raters.total - raters.names.length) : 0;
    return (
        <TooltipProvider>
            <Tooltip open={open} onOpenChange={setOpen} delayDuration={150}>
                <TooltipTrigger asChild>
                    <span>{chip}</span>
                </TooltipTrigger>
                <TooltipContent className="max-w-[240px] p-2">
                    {/* Inherit the tooltip's own foreground colour — an explicit
                        text-foreground here renders dark-on-dark and hides the names. */}
                    <div className="mb-1 flex justify-between gap-4 text-xs font-semibold opacity-70">
                        <span>{t("rating_rated_by")}</span>
                        <span>{raters?.total ?? count}</span>
                    </div>
                    <div className="flex flex-col gap-1">
                        {(raters?.names ?? []).map((n, i) => (
                            <span key={`${n}-${i}`} className="text-xs">{n}</span>
                        ))}
                        {extra > 0 ? (
                            <span className="text-xs opacity-70">+ {extra} {t("rating_more")}</span>
                        ) : null}
                    </div>
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
};

export default RatingChip;
