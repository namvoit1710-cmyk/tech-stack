import { cn } from "@ldc/ui";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { Clock } from "lucide-react";
import type { IWorkflow } from "../../types/workflows";

// A "day" of saved time is a WORKING day (8h), not a 24h calendar day — this
// humanizes saved human labour, so 8h of manual work = 1 working day (1d), not 1/3 of one.
export const WORK_DAY_SECONDS = 8 * 3600; // 28800

// SA-2751: humanize a duration in SECONDS → at most two units, working-day ceiling.
// "6h", "3d 3h" (= 27h of manual work), "2m 15s", "45s".
export const humanizeSeconds = (secs: number): string => {
    const s = Math.max(0, Math.round(secs));
    const d = Math.floor(s / WORK_DAY_SECONDS);
    const h = Math.floor((s % WORK_DAY_SECONDS) / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (d > 0) return h ? `${d}d ${h}h` : `${d}d`;
    if (h > 0) return m ? `${h}h ${m}m` : `${h}h`;
    if (m > 0) return sec ? `${m}m ${sec}s` : `${m}m`;
    return `${sec}s`;
};

export interface ManualWorkChipProps {
    manualWork?: IWorkflow["manual_work"];
    onClick?: () => void;
    className?: string;
}

// SA-2751: the "manual time saved" chip on a Workflows-tab row. Face shows only
// the humanized saved total; the per-run manual time + run count live in the
// tooltip. Renders nothing when unset (manual_seconds null) or saved is 0 —
// setting is done from the ⋯ menu (this chip is just a convenient edit shortcut).
export const ManualWorkChip = ({ manualWork, onClick, className }: ManualWorkChipProps) => {
    const manualSeconds = manualWork?.manual_seconds ?? 0;
    const runs = manualWork?.successful_runs ?? 0;
    const saved = manualWork?.saved_seconds ?? 0;
    if (!manualSeconds || saved <= 0) return null;

    const label = `Manual time saved: ${humanizeSeconds(saved)} (${humanizeSeconds(manualSeconds)} per run over ${runs} runs)`;

    return (
        <TooltipProvider>
            <Tooltip delayDuration={150}>
                <TooltipTrigger asChild>
                    <button
                        type="button"
                        aria-label={label}
                        onClick={(e) => { e.preventDefault(); onClick?.(); }}
                        className={cn(
                            "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-semibold",
                            "border-emerald-500/30 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
                            className,
                        )}
                    >
                        <Clock className="size-3" />
                        <span>{`Saved ${humanizeSeconds(saved)}`}</span>
                    </button>
                </TooltipTrigger>
                <TooltipContent className="p-2 text-xs">
                    {`${runs} successful runs × ${humanizeSeconds(manualSeconds)} = ${humanizeSeconds(saved)} saved`}
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
};

export default ManualWorkChip;
