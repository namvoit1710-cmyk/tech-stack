import type { ExecutionStatus } from "@/features/workflows/types/workflow-log";
import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";

const EXECUTION_STATUS_CLASS: Record<ExecutionStatus, string> = {
    PROCESSING: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
    SUCCESS: "bg-green-500/15 text-green-700 dark:text-green-400",
    ERROR: "bg-red-500/15 text-red-700 dark:text-red-400",
    TIMEOUT: "bg-muted text-muted-foreground",
};

interface ExecutionStatusBadgeProps {
    status: ExecutionStatus;
}

const ExecutionStatusBadge = ({ status }: ExecutionStatusBadgeProps) => {
    const { t } = useLanguage();
    return (
        <span
            className={cn(
                "text-[10px] font-semibold px-1.5 py-0.5 rounded uppercase tracking-wide",
                EXECUTION_STATUS_CLASS[status]
            )}
            title={t(`log.status.${status}`)}
        >
            {t(`log.status.${status}`)}
        </span>
    );
};

export default ExecutionStatusBadge;
