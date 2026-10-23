import Page from "@/components/containers/page";
import WorkerAppRow, { APP_ROW_GRID } from "@/features/workers/components/worker-app-row";
import WorkerAppRowsSkeleton from "@/features/workers/components/worker-app-rows-skeleton";
import WorkerSummaryCards from "@/features/workers/components/worker-summary-cards";
import { useQueryWorkerApps } from "@/features/workers/hooks/apis/use-worker-apps-query";
import { useWorkerHealthSync } from "@/features/workers/hooks/use-worker-health-sync";
import { useLanguage } from "@/hooks/use-language";
import { RefreshCw } from "lucide-react";

const WorkerHealthPage = () => {
    const { t } = useLanguage();
    const { data: rows, isLoading, isFetching, isError } = useQueryWorkerApps();
    // Subscribes and patches the cache. Its `connected` flag is deliberately
    // unused: the transport is plumbing, and a permanent "LIVE" chip in the
    // header is noise on a page whose whole job is reporting fleet state.
    useWorkerHealthSync();
    const list = rows ?? [];
    // First load has no data yet -> full overlay. A background refetch with data
    // already on screen -> only the subtle indicator, never the layout-shifting overlay.
    const refreshing = !!isFetching && !isLoading && list.length > 0;

    return (
        <Page>
            <Page.Header
                title={t("workers.page_title")}
                actions={
                    refreshing ? (
                        <span
                            data-testid="workers-refreshing"
                            className="flex items-center gap-1 text-xs text-muted-foreground"
                        >
                            <RefreshCw className="size-3 animate-spin" aria-hidden />
                            {t("workers.refreshing")}
                        </span>
                    ) : null
                }
            />
            <WorkerSummaryCards rows={list} isLoading={!!isLoading && list.length === 0} />
            <div className="flex-1 overflow-auto px-4 pb-6">
                <div className="relative rounded-xl border">
                    <div className={`${APP_ROW_GRID} border-b bg-muted/40 px-4 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground`}>
                        <span />
                        <span>{t("workers.col.worker")}</span>
                        <span>{t("workers.col.delivery")}</span>
                        <span>{t("workers.col.replicas")}</span>
                        <span>{t("workers.col.nodes")}</span>
                        <span>{t("workers.col.oldest")}</span>
                    </div>
                    {isError && <div className="px-4 py-6 text-sm text-destructive">{t("workers.load_error")}</div>}
                    {!isError && !isLoading && list.length === 0 && (
                        <div className="flex min-h-[240px] items-center justify-center px-4 py-6 text-center text-sm text-muted-foreground">
                            {t("workers.empty")}
                        </div>
                    )}
                    {list.map((row) => (
                        <WorkerAppRow key={row.worker_app} row={row} />
                    ))}
                    {/* First load: skeleton rows on the real grid instead of a
                        spinner over an empty box — the columns land where they
                        will stay. A refetch that already has rows on screen keeps
                        them and shows only the header indicator. */}
                    {isLoading && list.length === 0 && <WorkerAppRowsSkeleton />}
                    <div
                        data-testid="workers-loading"
                        role="status"
                        aria-label={t("workers.loading")}
                        aria-hidden={!isLoading}
                        className="sr-only"
                    >
                        {isLoading ? t("workers.loading") : null}
                    </div>
                </div>
            </div>
        </Page>
    );
};

export default WorkerHealthPage;
