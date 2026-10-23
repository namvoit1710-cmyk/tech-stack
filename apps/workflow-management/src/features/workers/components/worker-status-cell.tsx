import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { appReplicaStatus } from "@/features/workers/utils/worker-app-view";
import type { IWorkerAppAggregate } from "@/features/workers/types/worker-app";

interface WorkerStatusCellProps {
    row: IWorkerAppAggregate;
}

// text-sm to match every other cell in the table body — the row sets the size
// and nothing inside it opts out.
const PILL = "inline-flex items-center rounded-full border px-2 py-0.5 text-sm font-medium tabular-nums";

/**
 * One place for the four states, so the rule cannot diverge across the row and
 * the cards. Healthy is deliberately quiet: when 36 rows all carry a coloured
 * pill, colour stops being a signal. Only degraded and dead are coloured, and a
 * pending app is never coloured at all — there is nothing truthful to colour
 * (replicas_total/replicas_healthy are zeros standing in for "unknown", not a
 * real count — see IWorkerAppAggregate).
 *
 * The status rule itself is NOT re-derived here — appReplicaStatus (Task 18)
 * is the single source of truth. A second copy of that rule is the failure
 * this epic exists to fix.
 */
const WorkerStatusCell = ({ row }: WorkerStatusCellProps) => {
    const { t } = useLanguage();
    const status = appReplicaStatus(row);
    const count = `${row.replicas_healthy} / ${row.replicas_total}`;

    if (status === "pending") {
        return (
            <TooltipProvider>
                <Tooltip>
                    <TooltipTrigger asChild>
                        <span
                            data-testid="worker-status-cell"
                            data-status="pending"
                            className="text-muted-foreground cursor-help text-sm"
                        >
                            {t("workers.identity_pending")}
                        </span>
                    </TooltipTrigger>
                    <TooltipContent>{t("workers.identity_pending_hint")}</TooltipContent>
                </Tooltip>
            </TooltipProvider>
        );
    }

    if (status === "ok") {
        return (
            <span
                data-testid="worker-status-cell"
                data-status="ok"
                className="text-muted-foreground inline-flex items-center gap-2 text-sm tabular-nums"
            >
                <span className="bg-status-ok size-1.5 rounded-full" aria-hidden="true" />
                {count}
            </span>
        );
    }

    return (
        <span
            data-testid="worker-status-cell"
            data-status={status}
            className={cn(
                PILL,
                status === "warn"
                    ? "bg-status-warn/10 text-status-warn-fg border-status-warn/30"
                    : "bg-destructive/10 text-destructive border-destructive/30"
            )}
        >
            {count}
        </span>
    );
};

export default WorkerStatusCell;
