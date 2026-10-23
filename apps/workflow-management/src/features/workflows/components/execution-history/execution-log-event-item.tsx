import { Badge } from "@ldc/ui/components/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@ldc/ui/components/collapsible";
import { ChevronDown } from "lucide-react";
import { format } from "date-fns";
import { useState } from "react";
import { cn } from "@ldc/ui";
import type { IExecutionLogEvent } from "../../types/execution-log";
import { renderJson } from "./execution-log-json";

interface ExecutionLogEventItemProps {
    event: IExecutionLogEvent;
}

const EVENT_TYPE_COLORS: Record<string, string> = {
    RunStatusChanged: "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30",
    NodeInput: "bg-purple-500/15 text-purple-700 dark:text-purple-400 border-purple-500/30",
    NodeDispatched: "bg-orange-500/15 text-orange-700 dark:text-orange-400 border-orange-500/30",
    NodeCompleted: "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
    NodeFailed: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
    NodeOutput: "bg-teal-500/15 text-teal-700 dark:text-teal-400 border-teal-500/30",
};

const ExecutionLogEventItem = ({ event }: ExecutionLogEventItemProps) => {
    const [isOpen, setIsOpen] = useState(false);

    const timestamp = format(new Date(event.timestamp), "HH:mm:ss.SSS");
    const badgeColor = EVENT_TYPE_COLORS[event.event_type] || "bg-muted text-muted-foreground border-border";

    return (
        <Collapsible open={isOpen} onOpenChange={setIsOpen}>
            <CollapsibleTrigger className="w-full">
                <div className="flex items-center gap-3 p-3 hover:bg-muted/50 rounded-md border border-transparent hover:border-border transition-colors">
                    <ChevronDown
                        className={cn(
                            "h-4 w-4 text-muted-foreground transition-transform",
                            isOpen && "rotate-180"
                        )}
                    />
                    <span className="font-mono text-xs text-muted-foreground min-w-[90px]">
                        {timestamp}
                    </span>
                    <Badge variant="outline" className={cn("text-xs", badgeColor)}>
                        {event.event_type}
                    </Badge>
                    <span className="text-xs text-muted-foreground truncate flex-1 text-left">
                        {event.topic}
                    </span>
                </div>
            </CollapsibleTrigger>

            <CollapsibleContent>
                <div className="ml-7 mt-2 mb-3">
                    <pre className="bg-muted p-3 rounded-md text-xs font-mono text-left overflow-x-auto">
                        {renderJson(event.payload, 0)}
                    </pre>
                </div>
            </CollapsibleContent>
        </Collapsible>
    );
};

export default ExecutionLogEventItem;
