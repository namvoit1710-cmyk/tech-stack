import { useWorkflowLogStore } from "@/features/workflows/stores/log-store";
import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { ChevronDown } from "lucide-react";
import { useMemo, useState } from "react";
import LogEmptyState from "./log-empty-state";
import LogList from "./log-list";

interface LogSectionProps {
    workflowId: string;
}

const LogSection = ({ workflowId }: LogSectionProps) => {
    const { t } = useLanguage();

    const [isCollapsed, setIsCollapsed] = useState(true);

    const allLogs = useWorkflowLogStore((s) => s.logs);
    const clearLogs = useWorkflowLogStore((s) => s.clearLogs);

    const workflowLogs = useMemo(
        () => allLogs.filter((l) => l.workflowId === workflowId),
        [allLogs, workflowId]
    );

    return (
        <section className={cn("flex flex-col overflow-hidden border-t border-border")}>
            <div className="flex items-center gap-3 px-4 py-2 border-b border-border">
                <div className="flex items-center gap-1 flex-2">
                    <Button
                        variant="ghost"
                        size="icon"
                        className="cursor-pointer rounded-md hover:bg-muted size-6 flex items-center justify-center shrink-0"
                        onClick={() => setIsCollapsed((prev) => !prev)}
                    >
                        <ChevronDown className={cn("transition-transform duration-300 ease-in-out size-4", isCollapsed ? "rotate-180" : "")} />
                    </Button>

                    <h3 className="text-xs font-semibold tracking-widest text-muted-foreground uppercase">
                        {t("log.title")}
                    </h3>

                    <span className="flex items-center gap-1 text-[10px] font-semibold text-green-500 uppercase tracking-widest">
                        <span className="size-1.5 rounded-full bg-green-500 animate-pulse" />
                        {t("log.live")}
                    </span>
                </div>

                <div className="flex items-center justify-end gap-4">
                    <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                        {workflowLogs.length}
                    </span>

                    {workflowLogs.length > 0 && (
                        <Button
                            variant="ghost"
                            onClick={clearLogs}
                            className="flex items-center justify-center shrink-0 ml-auto text-xs cursor-pointer text-red-400 hover:text-red-600 transition-colors"
                        >
                            {t("log.clear")}
                        </Button>
                    )}
                </div>
            </div>

            <div
                className={cn(
                    "flex flex-col overflow-hidden transition-all duration-300 ease-in-out",
                    isCollapsed ? "h-0" : "h-[280px]"
                )}
            >

                <div className="flex-1 overflow-y-auto px-1">
                    {!workflowLogs.length && (
                        <div className="h-full">
                            <LogEmptyState />
                        </div>
                    )}

                    {!!workflowLogs.length && (
                        <LogList logs={workflowLogs} />
                    )}
                </div>
            </div>
        </section>
    );
};

export default LogSection;
