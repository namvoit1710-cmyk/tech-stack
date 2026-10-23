import { LOG_ACTION_ICON } from "@/constants/log";
import type { IWorkflowLogs } from "@/features/workflows/types/workflow-log";
import { formatDuration, formatLogTime, getExecutionDuration } from "@/features/workflows/utils/workflow-log-utils";
import { useLanguage } from "@/hooks/use-language";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@ldc/ui/components/collapsible";
import { DynamicNodeIcon } from "@ldc/workflow-editor";
import { memo, useState } from "react";
import ExecutionStatusBadge from "./execution-status-badge";

interface LogItemProps {
    log: IWorkflowLogs;
}

const LogItem = ({ log }: LogItemProps) => {
    const { t } = useLanguage();
    const [isErrorExpanded, setIsErrorExpanded] = useState(false);
    const hasError = log.status === "ERROR" && log.errorMessage;

    return (
        <Collapsible open={isErrorExpanded} onOpenChange={setIsErrorExpanded}>
            <CollapsibleTrigger asChild>
                <div className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-muted/50 rounded-md transition-colors">
                    <span className="shrink-0 flex items-center justify-center">
                        <DynamicNodeIcon name={LOG_ACTION_ICON[log.actionName]} className="text-base size-4!" />
                    </span>

                    <span className="text-foreground shrink-0 w-36 truncate line-clamp-1">
                        {t(`log.actions.${log.actionName}`)}
                    </span>

                    {log.nodeName && (
                        <span className="text-muted-foreground truncate flex-2" title={log.nodeName}>
                            {log.nodeName}
                        </span>
                    )}

                    <div className="ml-auto flex items-center gap-2 shrink-0">
                        {log.status && (
                            <ExecutionStatusBadge status={log.status} />
                        )}

                        <span className="text-muted-foreground text-xs tabular-nums text-end w-16 truncate">
                            {formatLogTime(log.timestamp)}
                        </span>

                        <span className="text-muted-foreground text-xs text-end shrink-0 min-w-16 truncate">
                            {formatDuration(getExecutionDuration(log))} {t("log.ago")}
                        </span>
                    </div>
                </div>
            </CollapsibleTrigger>

            {hasError && (
                <CollapsibleContent>
                    <div className="px-3 pb-2 pt-1 text-xs">
                        <div className="bg-red-500/10 border border-red-500/30 rounded px-2 py-1.5 text-red-700 dark:text-red-400">
                            <p className="font-medium mb-1">{t("log.status.ERROR")}:</p>
                            <pre className="whitespace-pre-wrap wrap-break-word font-mono text-xs">
                                {log.errorMessage}
                            </pre>
                        </div>
                    </div>
                </CollapsibleContent>
            )}
        </Collapsible>
    );
};

export default memo(LogItem);
