import { Button } from "@ldc/ui/components/button";
import { Skeleton } from "@ldc/ui/components/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@ldc/ui/components/tabs";
import { cn } from "@ldc/ui";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { useQueryClient } from "@ldc/tanstack-query";
import { Ban, Boxes, History, List, RefreshCw, RotateCcw, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { WORKFLOW_EXECUTION_STATUS_ENUM } from "@/constants/workflows";
import { useExecutionLogDetail, useRunWorkflow, useStopWorkflow, workflowKey } from "../../hooks/apis/workflows";
import ExecutionLogSummary from "./execution-log-summary";
import ExecutionLogEventItem from "./execution-log-event-item";
import ExecutionLogByNode from "./execution-log-by-node";
import { useLanguage } from "@/hooks/use-language";

// Detail view can be read two ways: the raw event timeline ("event") or the
// same events regrouped per node showing each node's input/output ("node", SA-2710).
type ExecutionLogViewMode = "event" | "node";

// ponytail: a run is "in-flight" (cancellable, not re-runnable) in exactly these states.
const IN_FLIGHT_STATUSES = new Set<string>([
    WORKFLOW_EXECUTION_STATUS_ENUM.CREATED,
    WORKFLOW_EXECUTION_STATUS_ENUM.PENDING,
    WORKFLOW_EXECUTION_STATUS_ENUM.RUNNING,
    WORKFLOW_EXECUTION_STATUS_ENUM.PAUSED,
]);

interface ExecutionLogSidebarProps {
    runId: string;
    onClose: () => void;
}

const ExecutionLogSidebar = ({ runId, onClose }: ExecutionLogSidebarProps) => {
    const { t } = useLanguage();
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [viewMode, setViewMode] = useState<ExecutionLogViewMode>("node");
    const { data, isLoading, isFetching, error, refetch } = useExecutionLogDetail(runId);

    const summary = data?.data.summary;
    const workflowId = summary?.workflow_id;
    const isInFlight = !!summary && IN_FLIGHT_STATUSES.has(summary.status.toLowerCase());

    const invalidateRuns = () => queryClient.invalidateQueries({ queryKey: workflowKey.all });

    const { mutate: cancelRun, isPending: isCancelling } = useStopWorkflow({
        onSuccess: () => { toast.success(t("execution_log_cancel_success")); void invalidateRuns(); },
        onError: (err) => toast.error(t("execution_log_action_failed"), err.message),
    });

    const { mutate: rerunWorkflow, isPending: isRerunning } = useRunWorkflow({
        onSuccess: () => { toast.success(t("execution_log_rerun_success")); void invalidateRuns(); },
        onError: (err) => toast.error(t("execution_log_action_failed"), err.message),
    });

    const sortedEvents = useMemo(() => {
        return [...(data?.data.events ?? [])].sort((left, right) => left.index - right.index);
    }, [data?.data.events]);

    return (
        <div className="h-full flex flex-col bg-background">
            <div className="flex items-center justify-between px-4 py-1 border-b bg-accent text-accent-foreground">
                <h2 className="text-lg font-semibold">{t("execution_log_detail_title")}</h2>
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={onClose}
                    className="h-8 w-8 hover:bg-primary-foreground/20"
                >
                    <X className="h-4 w-4" />
                </Button>
            </div>

            {/* Run-control actions — operate on the run shown in this detail view. */}
            <div className="flex items-center gap-2 px-4 py-2 border-b">
                <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
                    <RefreshCw className={cn("size-4", isFetching && "animate-spin")} />
                    {t("execution_log_action_refresh")}
                </Button>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => cancelRun(runId)}
                    disabled={!isInFlight || isCancelling}
                >
                    <Ban className="size-4" />
                    {t("execution_log_action_cancel")}
                </Button>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => workflowId && rerunWorkflow({ workflowId })}
                    disabled={isInFlight || !workflowId || isRerunning}
                >
                    <RotateCcw className="size-4" />
                    {t("execution_log_action_rerun")}
                </Button>
                {/* Opens the read-only debug view: workflow canvas (left) + replay
                    scrubber (right). Distinct from Rerun — replay never mutates. */}
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => workflowId && navigate(`/workflow/${workflowId}/replay/${runId}`)}
                    disabled={!summary || !workflowId}
                >
                    <History className="size-4" />
                    Replay
                </Button>
            </div>

            <div className="flex-1 overflow-hidden">
                {isLoading && (
                    <>
                        <div className="p-4 border-b space-y-3">
                            <Skeleton className="h-6 w-32" />
                            <Skeleton className="h-6 w-24" />
                            <Skeleton className="h-6 w-28" />
                        </div>

                        <div className="p-4 space-y-2">
                            {Array.from({ length: 8 }).map((_, i) => (
                                <Skeleton key={i} className="h-12 w-full" />
                            ))}
                        </div>
                    </>
                )}

                {error && (
                    <div className="p-4">
                        <div className="bg-red-500/15 text-red-700 p-4 rounded-md border border-red-500/30">
                            <p className="font-semibold mb-2">{t("execution_log_load_error_title")}</p>
                            <p className="text-sm">{(error as Error).message}</p>
                        </div>
                    </div>
                )}

                {data && (
                    <div className="h-full flex flex-col overflow-hidden">
                        <ExecutionLogSummary summary={data.data.summary} />

                        {/* View-mode toggle: By Event (timeline) vs By Node (per-node I/O). SA-2710.
                            Both views read the one already-loaded event list — switching never refetches. */}
                        <Tabs
                            value={viewMode}
                            onValueChange={(value) => setViewMode(value as ExecutionLogViewMode)}
                            className="flex-1 overflow-hidden px-4 pt-3 gap-0"
                        >
                            <TabsList variant="line" className="mb-2">
                                <TabsTrigger value="node">
                                    <Boxes className="size-4" />
                                    {t("execution_log_view_by_node")}
                                </TabsTrigger>
                                <TabsTrigger value="event">
                                    <List className="size-4" />
                                    {t("execution_log_view_by_event")}
                                </TabsTrigger>
                            </TabsList>

                            <TabsContent value="event" className="overflow-y-auto pb-4">
                                <h3 className="text-sm font-semibold mb-3 text-muted-foreground">
                                    {t("execution_log_events")} ({sortedEvents.length})
                                </h3>
                                {sortedEvents.length === 0 ? (
                                    <p className="text-sm text-muted-foreground text-center py-8">
                                        {t("execution_log_no_events")}
                                    </p>
                                ) : (
                                    <div className="space-y-1">
                                        {sortedEvents.map((event) => (
                                            <ExecutionLogEventItem key={event.event_id} event={event} />
                                        ))}
                                    </div>
                                )}
                            </TabsContent>

                            <TabsContent value="node" className="overflow-y-auto pb-4">
                                <ExecutionLogByNode events={sortedEvents} />
                            </TabsContent>
                        </Tabs>
                    </div>
                )}
            </div>
        </div>
    );
};

export default ExecutionLogSidebar;
