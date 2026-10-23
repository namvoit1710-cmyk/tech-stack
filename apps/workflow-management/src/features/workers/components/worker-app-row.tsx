import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@ldc/ui/components/collapsible";
import { Skeleton } from "@ldc/ui/components/skeleton";
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import WorkerAppNodesPopover from "@/features/workers/components/worker-app-nodes-popover";
import WorkerStatusCell from "@/features/workers/components/worker-status-cell";
import { useQueryWorkerAppInstances } from "@/features/workers/hooks/apis/use-worker-app-instances-query";
import type { IWorkerAppAggregate } from "@/features/workers/types/worker-app";
import { humaniseAge } from "@/features/workers/utils/worker-app-view";

/** Exported so the page header can share this exact template — header and
 *  rows built off one grid string is what keeps columns lined up. */
export const APP_ROW_GRID = "grid grid-cols-[24px_1.6fr_0.6fr_1fr_0.8fr_1fr] items-center gap-2";

/** Column template for the nested per-process table. Its own grid, not the
 *  parent's: these are different columns about a different unit (one PROCESS,
 *  not one app), and forcing them onto the app grid would align headings that
 *  mean nothing to each other. */
const INSTANCE_GRID = "grid grid-cols-[1.6fr_0.9fr_1.2fr_0.9fr_0.6fr] items-center gap-3";

/** Mirrors the executor's HEALTHY_STATUSES (None/""/healthy/active) — blank is
 *  healthy there, so a worker that never set a status is not painted as down. */
const isInstanceHealthy = (status: string): boolean =>
    status === "" || status === "healthy" || status === "active";

interface WorkerAppRowProps {
    row: IWorkerAppAggregate;
}

const WorkerAppRow = ({ row }: WorkerAppRowProps) => {
    const { t } = useLanguage();
    const [open, setOpen] = useState(false);
    const { data: instances, isLoading, isError } = useQueryWorkerAppInstances(row.worker_app, open);

    // How many process rows to expect. "pending" means the registry does not
    // know, so guess one rather than render a count it just told us is unknown.
    const skeletonRowCount =
        row.replica_identity === "known" ? Math.min(Math.max(row.replicas_total, 1), 8) : 1;

    /* Same uppercase-11px treatment as the page header row — that shared
       styling is what reads as "column label" rather than "smaller data".
       Shared by the skeleton and the loaded list so the header never moves. */
    const instanceHeader = (
        <div
            className={cn(
                INSTANCE_GRID,
                "text-muted-foreground border-b py-1.5 pr-4 pl-12 text-[11px] font-medium tracking-wide uppercase"
            )}
        >
            <span>{t("workers.inst.process")}</span>
            <span>{t("workers.inst.status")}</span>
            <span>{t("workers.inst.host")}</span>
            <span>{t("workers.inst.heartbeat")}</span>
            <span>{t("workers.inst.version")}</span>
        </div>
    );

    return (
        <Collapsible open={open} onOpenChange={setOpen}>
            {/* Plain div, not a trigger: WorkerAppNodesPopover renders its own
                <button>, and a <button> cannot legally contain another
                interactive element (browsers may reparent the markup; AT and
                keyboard order both break). Only the chevron cell below is the
                real CollapsibleTrigger, so nothing nests. Whole-row click is
                traded away for a correct content model. */}
            {/* text-sm on the row, not per cell: every cell in the table body
                renders at one size. Only the column header stays smaller — it
                is a label, not data. */}
            <div className={cn(APP_ROW_GRID, "hover:bg-muted/50 border-b px-4 py-3 text-sm")}>
                <CollapsibleTrigger
                    aria-label={`${t("workers.expand_processes")} ${row.worker_app}`}
                    className="text-muted-foreground hover:text-foreground flex size-6 items-center justify-center rounded"
                >
                    <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} />
                </CollapsibleTrigger>
                <span className="text-left font-semibold">{row.worker_app}</span>
                <span className="text-left">
                    <Badge variant="outline" className="text-sm">
                        {row.delivery_mode}
                    </Badge>
                </span>
                <span className="text-left">
                    <WorkerStatusCell row={row} />
                </span>
                <span className="text-left">
                    <WorkerAppNodesPopover count={row.nodes_total} nodes={row.node_types ?? []} />
                </span>
                <span className="text-muted-foreground text-left tabular-nums">
                    {humaniseAge(row.oldest_heartbeat_age_seconds)}
                </span>
            </div>
            <CollapsibleContent>
                {/* Rows, not a card. The processes belong to the same table as the
                    app above them, so they keep the table's rhythm — full-bleed,
                    one border-b per row, one horizontal padding — and say "nested"
                    with an indent and a tint instead of a box inside a box. */}
                <div className="bg-muted/20">
                    {isError && (
                        <div className="text-destructive border-b py-2 pr-4 pl-12 text-sm">
                            {t("workers.instance_error")}
                        </div>
                    )}
                    {!isError && isLoading && (
                        <>
                            {instanceHeader}
                            {/* Skeleton rows on the SAME grid as the real ones, and
                                as many as the app has processes — the summary row
                                already told us that number, so the list does not
                                jump when the data lands. */}
                            {Array.from({ length: skeletonRowCount }, (_, i) => (
                                <div
                                    key={i}
                                    data-testid="worker-app-instance-skeleton"
                                    className={cn(INSTANCE_GRID, "border-b py-2.5 pr-4 pl-12")}
                                >
                                    <Skeleton className="h-3.5 w-32" />
                                    <Skeleton className="h-3.5 w-16" />
                                    <Skeleton className="h-3.5 w-28" />
                                    <Skeleton className="h-3.5 w-14" />
                                    <Skeleton className="h-3.5 w-10" />
                                </div>
                            ))}
                        </>
                    )}
                    {!isError && !isLoading && instances?.length === 0 && (
                        <div className="text-muted-foreground border-b py-2 pr-4 pl-12 text-sm">
                            {t("workers.no_instances")}
                        </div>
                    )}
                    {!isError && !!instances?.length && (
                        <>
                            {instanceHeader}
                            {instances.map((inst) => (
                                <div
                                    key={inst.instance_id}
                                    data-testid="worker-app-instance-row"
                                    className={cn(INSTANCE_GRID, "border-b py-2 pr-4 pl-12 text-sm")}
                                >
                                    <span className="truncate font-mono">{inst.instance_id}</span>
                                    <span className="text-muted-foreground inline-flex items-center gap-2">
                                        <span
                                            className={cn(
                                                "size-1.5 shrink-0 rounded-full",
                                                isInstanceHealthy(inst.status) ? "bg-status-ok" : "bg-destructive"
                                            )}
                                            aria-hidden="true"
                                        />
                                        {inst.status || t("workers.inst.unknown_status")}
                                    </span>
                                    <span className="text-muted-foreground truncate">{inst.host || "—"}</span>
                                    <span className="text-muted-foreground tabular-nums">
                                        {humaniseAge(inst.age_seconds)}
                                    </span>
                                    <span className="text-muted-foreground tabular-nums">{inst.version || "—"}</span>
                                </div>
                            ))}
                        </>
                    )}
                </div>
            </CollapsibleContent>
        </Collapsible>
    );
};

export default WorkerAppRow;
