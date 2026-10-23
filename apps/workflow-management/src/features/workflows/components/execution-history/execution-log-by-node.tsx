import { Badge } from "@ldc/ui/components/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@ldc/ui/components/collapsible";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { cn } from "@ldc/ui";
import { ArrowDownToLine, ArrowUpFromLine, ChevronDown, Copy, TriangleAlert } from "lucide-react";
import { format } from "date-fns";
import { useMemo, useState } from "react";
import type { IExecutionLogEvent } from "../../types/execution-log";
import { renderJson } from "./execution-log-json";

// "By Node" view — the same execution-log events, regrouped by node_id so the
// operator can read one node's INPUT and OUTPUT at a glance (SA-2710). No new
// backend call: everything below is derived from the events the "By Event"
// timeline already loaded.

type NodeStatus = "completed" | "failed" | "running" | "skipped" | "pending";

interface INodeIO {
    nodeId: string;
    nodeName: string;
    workerType?: string;
    status: NodeStatus;
    input?: unknown;
    output?: unknown;
    error?: { code?: string; message?: string };
    // Wall-clock time of the node's first event, shown like the By Event timeline
    // (HH:mm:ss.SSS). We don't show a duration — the backend's execution_time_ms
    // is always 0, and the timeline reads more naturally per node.
    timestamp?: string;
    firstIndex: number;
}

const STATUS_BADGE: Record<NodeStatus, string> = {
    completed: "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
    failed: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
    running: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30",
    skipped: "bg-muted text-muted-foreground border-border",
    pending: "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30",
};

const asRecord = (v: unknown): Record<string, unknown> =>
    v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

const str = (v: unknown): string | undefined => (typeof v === "string" && v.length > 0 ? v : undefined);

// The node's produced output can live under a few different keys depending on
// the emitting node. NodeOutputProduced carries it under state_updates[node_id];
// otherwise probe the common output keys, else fall back to the payload minus
// the routing/context noise.
const extractOutput = (payload: Record<string, unknown>, nodeId: string): unknown => {
    const stateUpdates = asRecord(payload.state_updates);
    if (nodeId in stateUpdates && stateUpdates[nodeId] != null) return stateUpdates[nodeId];
    for (const key of ["output_data", "output", "result", "data"]) {
        if (key in payload && payload[key] != null) return payload[key];
    }
    if (Object.keys(stateUpdates).length > 0) return stateUpdates;
    const {
        event_id, timestamp, source, root_run_id, depth, metadata, run_id, workflow_id,
        task_id, node_id, node_name, status, correlation_id, callback_topic,
        execution_time_ms, output_port, taken_edge_ports, ...rest
    } = payload;
    return Object.keys(rest).length > 0 ? rest : payload;
};

// Fold the flat event stream into one record per node, in execution order.
const groupEventsByNode = (events: IExecutionLogEvent[]): INodeIO[] => {
    const byNode = new Map<string, INodeIO>();

    for (const event of events) {
        const payload = asRecord(event.payload);
        const nodeId = str(payload.node_id) ?? str((payload as { nodeId?: unknown }).nodeId);
        if (!nodeId || nodeId === "__bootstrap__") continue; // run-level / synthetic events have no real node

        let node = byNode.get(nodeId);
        if (!node) {
            node = { nodeId, nodeName: nodeId, status: "pending", firstIndex: event.index };
            byNode.set(nodeId, node);
        }
        const name = str(payload.node_name);
        if (name) node.nodeName = name;
        // events arrive index-sorted, so the first one seen is the node's start time
        if (!node.timestamp) node.timestamp = event.timestamp;

        switch (event.event_type) {
            case "NodeInput":
            case "NodeStarted":
            case "NodeDispatched": {
                const input = payload.input;
                if (input != null) node.input = input;
                const wt = str(payload.worker_type);
                if (wt) node.workerType = wt;
                if (node.status === "pending") node.status = "running";
                break;
            }
            case "NodeOutput":
            case "NodeOutputProduced": {
                node.output = extractOutput(payload, nodeId);
                break;
            }
            case "NodeCompleted": {
                node.status = "completed";
                break;
            }
            case "NodeFailed": {
                node.status = "failed";
                node.error = { code: str(payload.error_code), message: str(payload.error_message) };
                break;
            }
            case "NodeCancelled":
            case "NodeSkipped": {
                if (node.status === "pending" || node.status === "running") node.status = "skipped";
                break;
            }
            default:
                break;
        }
    }

    return [...byNode.values()].sort((a, b) => a.firstIndex - b.firstIndex);
};

const formatTs = (ts?: string): string | undefined => {
    if (!ts) return undefined;
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? undefined : format(d, "HH:mm:ss.SSS");
};

const JsonBlock = ({ label, value, tone }: { label: string; value: unknown; tone: "in" | "out" }) => {
    const text = useMemo(() => JSON.stringify(value ?? {}, null, 2), [value]);
    const copy = () => {
        void navigator.clipboard?.writeText(text);
        toast.success("Copied");
    };
    return (
        <div>
            <div className="flex items-center justify-between mb-1">
                <span
                    className={cn(
                        "flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide",
                        tone === "in" ? "text-blue-600 dark:text-blue-400" : "text-green-600 dark:text-green-400"
                    )}
                >
                    {tone === "in" ? <ArrowDownToLine className="size-3" /> : <ArrowUpFromLine className="size-3" />}
                    {label}
                </span>
                <button
                    type="button"
                    onClick={copy}
                    className="text-muted-foreground hover:text-foreground transition-colors"
                    aria-label={`Copy ${label}`}
                >
                    <Copy className="size-3" />
                </button>
            </div>
            <pre className="bg-muted p-3 rounded-md text-xs font-mono text-left overflow-x-auto max-h-56 overflow-y-auto">
                {renderJson(value ?? {}, 0)}
            </pre>
        </div>
    );
};

const NodeCard = ({ node, index }: { node: INodeIO; index: number }) => {
    const [isOpen, setIsOpen] = useState(index === 0);
    const timestamp = formatTs(node.timestamp);
    const hasIO = node.status !== "skipped";

    return (
        <Collapsible open={isOpen} onOpenChange={setIsOpen}>
            <div className={cn("rounded-md border", node.status === "failed" ? "border-red-500/40" : "border-border")}>
                <CollapsibleTrigger className="w-full" disabled={!hasIO}>
                    <div className="flex items-center gap-2 p-3 hover:bg-muted/50 rounded-md transition-colors text-left">
                        <span className="font-mono text-xs text-muted-foreground w-5 text-right">{index + 1}</span>
                        {hasIO ? (
                            <ChevronDown
                                className={cn("size-4 text-muted-foreground transition-transform", isOpen && "rotate-180")}
                            />
                        ) : (
                            <span className="w-4" />
                        )}
                        <span className="font-semibold text-sm truncate">{node.nodeName}</span>
                        {node.workerType && (
                            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground bg-muted border border-border rounded px-1.5 py-0.5">
                                {node.workerType}
                            </span>
                        )}
                        <Badge variant="outline" className={cn("text-xs capitalize", STATUS_BADGE[node.status])}>
                            {node.status}
                        </Badge>
                        {timestamp && <span className="ml-auto font-mono text-xs text-muted-foreground">{timestamp}</span>}
                    </div>
                </CollapsibleTrigger>

                <CollapsibleContent>
                    <div className="px-3 pb-3 pt-1 space-y-3 border-t border-dashed">
                        <JsonBlock label="Input" value={node.input} tone="in" />
                        {node.status === "failed" ? (
                            <div>
                                <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-red-600 dark:text-red-400 mb-1">
                                    <TriangleAlert className="size-3" />
                                    Output — error
                                </span>
                                <div className="bg-red-500/15 text-red-700 dark:text-red-400 border border-red-500/30 rounded-md p-3 text-xs font-mono">
                                    {[node.error?.code, node.error?.message].filter(Boolean).join(" · ") || "Node failed"}
                                </div>
                            </div>
                        ) : node.status === "running" && node.output == null ? (
                            <div>
                                <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Output</span>
                                <p className="text-xs text-muted-foreground italic mt-1">waiting…</p>
                            </div>
                        ) : (
                            <JsonBlock label="Output" value={node.output} tone="out" />
                        )}
                    </div>
                </CollapsibleContent>
            </div>
        </Collapsible>
    );
};

interface ExecutionLogByNodeProps {
    events: IExecutionLogEvent[];
}

const ExecutionLogByNode = ({ events }: ExecutionLogByNodeProps) => {
    const nodes = useMemo(() => groupEventsByNode(events), [events]);

    if (nodes.length === 0) {
        return <p className="text-sm text-muted-foreground text-center py-8">No nodes executed yet</p>;
    }

    return (
        <div className="space-y-2">
            {nodes.map((node, i) => (
                <NodeCard key={node.nodeId} node={node} index={i} />
            ))}
        </div>
    );
};

export default ExecutionLogByNode;
