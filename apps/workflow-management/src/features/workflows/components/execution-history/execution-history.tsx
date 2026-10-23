import { useLanguage } from "@/hooks/use-language";
import { keepPreviousData } from "@ldc/tanstack-query";
import { cn } from "@ldc/ui";
import { ResizablePanelGroup, ResizablePanel } from "@ldc/ui/components/resizable";
import { useCallback, useMemo } from "react";
import { useWorkflowExecutionList, useRunRatings } from "../../hooks/apis/workflows";
import { useExecutionHistoryRealtime } from "../../hooks/use-execution-history-realtime";
import useSearchParamsQuery from "../../hooks/use-search-params-query";
import { useUIPanelStore } from "../../stores/ui-panel-stores";
import type { IWorkflowExecutionParams } from "../../types/execution";
import ExecutionFilter from "./execution-filter";
import ExecutionLogSidebar from "./execution-log-sidebar";
import ExecutionRunRow from "./execution-run-row";
import { ListPager } from "../common/list-pager";

export interface IWorkflowExecutionHistoryProps {
    isActive?: boolean;
}

const PAGE_SIZE = 20

const WorkflowExecutionHistory = ({ isActive }: IWorkflowExecutionHistoryProps) => {
    const { t } = useLanguage();
    const {
        currentPage,
        setCurrentPage,
        workflowId,
        setWorkflowId,
        executionStatus,
        sorting,
        setExecutionStatus,
        runId,
        setRunId,
        environmentId,
        setEnvironmentId,
    } = useSearchParamsQuery({ defaultSorting: [{ id: "started_at", desc: true }] });

    // SA-2704: run-id filter (shareable deep link). When a run id is pinned the
    // list shows just that one run (root or sub-run) auto-expanded with its
    // sub-runs; the other selectors and the pager are suppressed.
    const activeRunId = (runId ?? "").trim();
    const isRunIdFilter = activeRunId.length > 0;

    const isLogSidebarOpen = useUIPanelStore((s) => s.isLogSidebarOpen);
    const selectedRunId = useUIPanelStore((s) => s.selectedRunId);
    const setIsLogSidebarOpen = useUIPanelStore((s) => s.setIsLogSidebarOpen);
    const setSelectedRunId = useUIPanelStore((s) => s.setSelectedRunId);

    const handleCloseLogSidebar = useCallback(() => {
        setIsLogSidebarOpen(false);
        setSelectedRunId(null);
    }, [setIsLogSidebarOpen, setSelectedRunId]);


    const params = useMemo(() => {
        // SA-2704: run-id filter takes precedence — pin to exactly one run. Drop
        // roots_only (so a linked sub-run also resolves) and the other filters.
        if (isRunIdFilter) {
            return {
                $skip: 0,
                $top: 1,
                run_id: activeRunId,
            } satisfies IWorkflowExecutionParams;
        }

        const queryParams: IWorkflowExecutionParams = {
            $skip: (currentPage - 1) * PAGE_SIZE,
            $top: PAGE_SIZE,
            // Show only top-level runs; sub-runs are revealed by expanding a row.
            roots_only: true,
        };
        const filterParts: string[] = [];
        if (workflowId && workflowId !== "all") {
            filterParts.push(`workflow_id eq '${workflowId}'`);
        }
        if (executionStatus) {
            // Server stores run status uppercase (RunStatus enum); the select's option
            // values are lowercase, so normalize or the filter matches zero rows.
            filterParts.push(`status eq '${executionStatus.toUpperCase()}'`);
        }
        // SA-2723: narrow to runs that used a specific environment (server-side,
        // matches the ENVIRONMENT_ID stamped on the run).
        if (environmentId && environmentId !== "all") {
            filterParts.push(`environment_id eq '${environmentId}'`);
        }
        if (filterParts.length > 0) {
            queryParams.$filter = filterParts.join(" and ");
        }
        if (sorting?.length > 0) {
            queryParams.$orderby = sorting.map((s) => `${s.id} ${s.desc ? "desc" : "asc"}`).join(", ");
        }
        return queryParams;
    }, [currentPage, workflowId, executionStatus, environmentId, sorting, isRunIdFilter, activeRunId]);

    const { data: executionResponse, isLoading, isPlaceholderData } = useWorkflowExecutionList(params, {
        enabled: !!isActive,
        placeholderData: keepPreviousData,
        // SA-2670: no interval poll — realtime socket drives updates. Refetch on
        // focus too, to pick up runs started elsewhere while the screen was idle.
        refetchOnWindowFocus: true,
    });

    const tableData = useMemo(() => {
        return (executionResponse?.data?.items ?? []).map(i => ({ ...i, _id: i.id }))
    }, [executionResponse, isActive]);

    // SA-2825: one batched rating fetch for ALL visible top-level rows instead of
    // one call per row (N+1). Each row reads its slice from this shared map; the
    // query shares the `ratings()` key prefix so a rate/socket invalidation refetches
    // just this single call.
    const runIds = useMemo(() => tableData.map((r) => r.id), [tableData]);
    const { data: ratingsById } = useRunRatings(runIds, { enabled: !!isActive && runIds.length > 0 });

    const total = executionResponse?.data?.total ?? 0;
    // SA-2650: show the loader/dim only for the first load or a user-initiated
    // page/filter change (isPlaceholderData). Realtime socket refetches update
    // the list SILENTLY — no bar, no dim — so the chips don't flicker.
    const isBusy = isLoading || isPlaceholderData;

    // SA-2670: keep the list live via the realtime "runs:all" socket feed (same
    // push-gateway feed that colours nodes live in the editor). The hook only
    // joins the broadcast room while this tab is active, so a run triggered from
    // anywhere appears + updates live without polling or a manual refresh.
    useExecutionHistoryRealtime(!!isActive);

    return (
        <section className="flex flex-col h-full overflow-hidden gap-4 p-4">
            <ExecutionFilter
                isActive={isActive}
                initialFilter={{
                    workflowId,
                    executionStatus,
                    runId: runId ?? "",
                    environmentId,
                }}
                setWorkflowId={setWorkflowId}
                setExecutionStatus={setExecutionStatus}
                setRunId={setRunId}
                setEnvironmentId={setEnvironmentId}
            />

            <ResizablePanelGroup
                direction="horizontal"
                className="flex-1 border rounded-md overflow-hidden"
            >
                <ResizablePanel defaultSize={isLogSidebarOpen ? "70%" : "100%"} minSize={"20%"}>
                    <div className={cn("flex h-full flex-col gap-3 overflow-hidden p-3 transition-opacity", isBusy ? "opacity-60 pointer-events-none" : "")}>
                        {isBusy && <div className="loader-bar w-full" />}

                        {/* SA-2704: active run-id filter banner — shows the pinned run
                            id and a one-click Clear to return to the full list. */}
                        {isRunIdFilter ? (
                            <div className="flex items-center gap-2 rounded-md border border-blue-500/30 bg-blue-500/10 px-3 py-2 text-xs text-blue-700 dark:text-blue-400">
                                <span className="shrink-0">{t("filtered_to_run")}</span>
                                <span className="truncate font-mono">{activeRunId}</span>
                                <button
                                    type="button"
                                    className="ml-auto shrink-0 rounded border border-blue-500/30 px-2 py-0.5 font-medium hover:bg-blue-500/20"
                                    onClick={() => void setRunId?.("")}
                                >
                                    {t("clear_filter")}
                                </button>
                            </div>
                        ) : null}

                        <div className="flex-1 flex flex-col gap-2 overflow-y-auto pr-1">
                            {tableData.length === 0 && !isBusy ? (
                                <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
                                    {isRunIdFilter ? t("no_run_found") : t("no_executions")}
                                </div>
                            ) : (
                                tableData.map((e) => (
                                    <ExecutionRunRow key={e.id} run={e} defaultExpanded={isRunIdFilter} ratingsById={ratingsById} batched />
                                ))
                            )}
                        </div>

                        {/* Pager is meaningless when pinned to a single run. */}
                        {isRunIdFilter ? null : (
                            <ListPager
                                page={currentPage}
                                total={total}
                                pageSize={PAGE_SIZE}
                                onChange={(p) => void setCurrentPage(p)}
                                label={t("pagination_page", {
                                    current: currentPage,
                                    total: Math.max(1, Math.ceil(total / PAGE_SIZE)),
                                })}
                            />
                        )}
                    </div>
                </ResizablePanel>

                {isLogSidebarOpen && selectedRunId ? (
                    <>
                        <ResizablePanel defaultSize={"30%"} minSize={"30%"}>
                            <ExecutionLogSidebar
                                runId={selectedRunId}
                                onClose={handleCloseLogSidebar}
                            />
                        </ResizablePanel>
                    </>
                ) : null}
            </ResizablePanelGroup>
        </section>
    );
};

export default WorkflowExecutionHistory;
