import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Skeleton } from "@ldc/ui/components/skeleton";
import { useMemo } from "react";
import type { IWorkerAppAggregate } from "@/features/workers/types/worker-app";
import { summaryCardStats } from "@/features/workers/utils/worker-app-view";

interface WorkerSummaryCardsProps {
    rows: IWorkerAppAggregate[];
    /** First load, nothing fetched yet — show placeholders, never zeros. */
    isLoading?: boolean;
}

/**
 * Card maths are NOT re-derived here — summaryCardStats (Task 18) is the
 * single source of truth, including the rule that a pending app counts
 * toward `workers` but toward neither `degraded` nor `workersAtZero`, and
 * never enters the healthy/total ratio (unknown is not zero).
 *
 * The same honesty rule governs the loading state: before the first snapshot
 * lands there are no counts, and a rendered "0 / 0" states an outage that was
 * never measured. Placeholders say "not known yet", which is the truth.
 */
const WorkerSummaryCards = ({ rows, isLoading = false }: WorkerSummaryCardsProps) => {
    const { t } = useLanguage();
    const stats = useMemo(() => summaryCardStats(rows), [rows]);

    const cards = [
        { key: "workers", label: t("workers.cards.workers"), value: String(stats.workers), warn: false },
        { key: "healthy", label: t("workers.cards.healthy"), value: `${stats.healthy} / ${stats.total}`, warn: false },
        { key: "degraded", label: t("workers.cards.degraded"), value: String(stats.degraded), warn: stats.degraded > 0 },
        { key: "draining", label: t("workers.cards.draining"), value: String(stats.workersAtZero), warn: false },
    ];

    return (
        <div className="flex flex-wrap gap-3 px-4">
            {cards.map((c) => (
                <div key={c.key} className="min-w-[150px] rounded-xl border bg-card px-4 py-3">
                    <div className="text-xs text-muted-foreground">{c.label}</div>
                    <div
                        className={cn("mt-1 text-2xl font-bold", c.warn && "text-status-warn-fg")}
                        data-testid={`card-${c.key}`}
                    >
                        {isLoading ? <Skeleton className="my-1 h-6 w-12" /> : c.value}
                        {!isLoading && c.key === "healthy" && stats.pending > 0 && (
                            <span className="ml-2 text-xs font-normal text-muted-foreground">
                                {t("workers.cards.pending_suffix", { count: stats.pending })}
                            </span>
                        )}
                    </div>
                </div>
            ))}
        </div>
    );
};

export default WorkerSummaryCards;
