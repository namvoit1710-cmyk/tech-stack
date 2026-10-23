import CopyUtils from "@/components/utils/copy-utils";
import { Badge } from "@ldc/ui/components/badge";
import { cn } from "@ldc/ui";
import { differenceInSeconds } from "date-fns";
import type { IExecutionLogSummary } from "../../types/execution-log";
import { WORKFLOW_EXECUTION_STATUS_ENUM } from "@/constants/workflows";
import { useLanguage } from "@/hooks/use-language";

interface ExecutionLogSummaryProps {
    summary: IExecutionLogSummary;
}

const STATUS_BADGE_CLASS: Record<string, string> = {
    [WORKFLOW_EXECUTION_STATUS_ENUM.COMPLETED]: "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
    [WORKFLOW_EXECUTION_STATUS_ENUM.FAILED]: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
    [WORKFLOW_EXECUTION_STATUS_ENUM.RUNNING]: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30",
    [WORKFLOW_EXECUTION_STATUS_ENUM.CANCELLED]: "bg-muted text-muted-foreground border-border",
    [WORKFLOW_EXECUTION_STATUS_ENUM.PENDING]: "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30",
};

const ExecutionLogSummary = ({ summary }: ExecutionLogSummaryProps) => {
    const { t } = useLanguage();
    const duration = summary.completed_at
        ? differenceInSeconds(new Date(summary.completed_at), new Date(summary.started_at))
        : null;

    const normalizedStatus = summary.status.toLowerCase();
    const statusClass = STATUS_BADGE_CLASS[normalizedStatus] || STATUS_BADGE_CLASS[WORKFLOW_EXECUTION_STATUS_ENUM.PENDING];

    return (
        <div className="p-4 border-b grid grid-cols-2 gap-2">
            <div className="flex items-center gap-2 col-span-2">
                <span className="text-sm text-muted-foreground">{t("execution_log_summary_status")}:</span>
                <Badge variant="outline" className={cn("capitalize", statusClass)}>
                    {summary.status}
                </Badge>
            </div>

            {duration !== null && (
                <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">{t("execution_log_summary_duration")}:</span>
                    <span className="text-sm font-medium">{duration}s</span>
                </div>
            )}

            <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">{t("execution_log_summary_total_events")}:</span>
                <span className="text-sm font-medium">{summary.total_events}</span>
            </div>

            <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">{t("execution_log_summary_nodes")}:</span>
                <div className="flex gap-2 text-xs">
                    <Badge variant="outline" className="bg-green-500/15 text-green-700 border-green-500/30">
                        {summary.node_counts.completed} {t("execution_log_count_completed")}
                    </Badge>
                    {summary.node_counts.failed > 0 && (
                        <Badge variant="outline" className="bg-red-500/15 text-red-700 border-red-500/30">
                            {summary.node_counts.failed} {t("execution_log_count_failed")}
                        </Badge>
                    )}
                    {summary.node_counts.skipped > 0 && (
                        <Badge variant="outline" className="bg-gray-500/15 text-gray-700 border-gray-500/30">
                            {summary.node_counts.skipped} {t("execution_log_count_skipped")}
                        </Badge>
                    )}
                </div>
            </div>

            <div className="flex items-center gap-2 col-span-2">
                <span className="text-sm text-muted-foreground">{t("execution_log_summary_run_id")}:</span>
                <span className="font-mono text-xs text-muted-foreground truncate">
                    {summary.run_id}
                </span>
                <CopyUtils content={summary.run_id} />
            </div>
        </div>
    );
};

export default ExecutionLogSummary;
