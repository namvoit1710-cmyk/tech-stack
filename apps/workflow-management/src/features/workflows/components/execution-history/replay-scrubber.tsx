import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { Pagination, PaginationContent, PaginationItem } from "@ldc/ui/components/pagination";
import type { WorkflowEditorHandle } from "@ldc/workflow-editor";
import { ChevronLeft, ChevronRight, ShieldAlert, SkipBack, SkipForward, XCircle } from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { useCloseReplay, useOpenReplay, useReplayStep, useReplayTimelinePage } from "../../hooks/apis/workflows";
import { useEditorStore } from "../../stores/editor-stores";
import type { NodeExecutionStatus } from "../../types/execution";
import type { IReplayStateData, IReplayTimelineEntry } from "../../types/replay";
import { clampCursor, pageCount, pageOf, timelineLabel, TIMELINE_PAGE_SIZE } from "../../utils/replay-scrubber-utils";

interface ReplayScrubberProps {
    runId: string;
    onClose: () => void;
    // When mounted beside a read-only WorkflowEditor (the debug view), paint the
    // canvas nodes at the cursor via the editor handle — the same mechanism live
    // execution uses. Omitted in panel-only contexts.
    editorRef?: RefObject<WorkflowEditorHandle | null>;
}

// Map the backend's replay status onto the canvas node-status vocabulary
// (n8n-style overlay). "running" -> "executing"; skipped/cancelled -> "idle".
const toCanvasStatus = (status: string): NodeExecutionStatus =>
    status === "completed" ? "completed"
        : status === "failed" ? "failed"
            : status === "running" ? "executing"
                : "idle";

const Section = ({ title, value, testid }: { title: string; value: unknown; testid?: string }) => (
    <div>
        <div className="text-muted-foreground mb-1">{title}</div>
        <pre className="bg-muted rounded p-2 overflow-x-auto" data-testid={testid}>
            {JSON.stringify(value, null, 2)}
        </pre>
    </div>
);

/**
 * SA-1372 Replay Debugger — non-mutating time-travel over a run's recorded event
 * log, with an n8n-inspired debug UX: per-node canvas status (via
 * nodeExecutionMap), Input/Output inspector + taken-branch, inline error
 * surfacing, and an error-highlighted timeline. The run is never mutated
 * (distinct from Rerun); secrets are redacted server-side.
 */
const ReplayScrubber = ({ runId, onClose, editorRef }: ReplayScrubberProps) => {
    const [sessionId, setSessionId] = useState<string | null>(null);
    // First timeline page, shipped inside the open response — page 0 renders
    // without a second round-trip; later pages are fetched on demand.
    const [firstPage, setFirstPage] = useState<IReplayTimelineEntry[]>([]);
    const [total, setTotal] = useState(0);
    const [state, setState] = useState<IReplayStateData | null>(null);
    const [view, setView] = useState<"output" | "input">("output");
    // Long runs (1M+ events): the server pages the timeline (TIMELINE_PAGE_SIZE
    // per page) and the chips live in a fixed, scrollable frame so the transport
    // bar and Input/Output inspector below always stay reachable.
    const [page, setPage] = useState(0);
    const timelineRef = useRef<HTMLDivElement | null>(null);

    const setNodeExecution = useEditorStore((s) => s.setNodeExecution);

    const openReplay = useOpenReplay({
        onSuccess: (res) => {
            setSessionId(res.data.replay_session_id);
            setFirstPage(res.data.timeline);
            setTotal(res.data.total_steps);
        },
    });
    const stepReplay = useReplayStep(runId, sessionId ?? "", {
        onSuccess: (res) => setState(res.data),
    });
    const closeReplay = useCloseReplay();

    useEffect(() => {
        openReplay.mutate(runId);
        return () => {
            if (sessionId) closeReplay.mutate({ runId, sessionId });
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [runId]);

    useEffect(() => {
        if (sessionId) stepReplay.mutate({ op: "index", index: 0 });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sessionId]);

    // Paint the canvas nodes with the cursor's per-node status (n8n-style overlay).
    // node_status reflects status AT the cursor, so reset first then re-apply the
    // whole map — nodes that hadn't run yet fall back to idle. When a read-only
    // editor is mounted (debug view), drive its handle too and center on the
    // current node, mirroring how live execution paints the canvas.
    useEffect(() => {
        if (!state) return;
        const editor = editorRef?.current;
        editor?.resetAllNodeStatus?.();
        for (const [nodeId, status] of Object.entries(state.node_status)) {
            const canvasStatus = toCanvasStatus(status);
            setNodeExecution(nodeId, { status: canvasStatus });
            editor?.setNodeStatus?.(nodeId, canvasStatus);
        }
        if (state.node_id) editor?.centerOnNode?.(state.node_id);
    }, [state, setNodeExecution, editorRef]);

    const goTo = (index: number) => {
        if (sessionId) stepReplay.mutate({ op: "index", index: clampCursor(index, total) });
    };

    const cursor = state?.cursor ?? 0;
    const branchPorts = state?.node_io.taken_edge_ports
        ? Array.from(new Set(Object.values(state.node_io.taken_edge_ports)))
        : [];

    const pages = pageCount(total);
    // Pages after the first are fetched on demand; the recorded log is immutable
    // per session, so fetched pages cache forever. `placeholderData` keeps the
    // previous page's chips visible while the next page loads (no flash).
    const timelinePage = useReplayTimelinePage(
        runId, sessionId ?? "", page * TIMELINE_PAGE_SIZE, TIMELINE_PAGE_SIZE,
        { enabled: !!sessionId && page > 0, placeholderData: (prev) => prev },
    );
    const pageItems: IReplayTimelineEntry[] =
        page === 0 ? firstPage : (timelinePage.data?.data.items ?? []);

    // Follow the cursor: whenever it moves (scrub, prev/next, chip click), snap
    // to its page — entry `index` is linear, so its 0-based position is index-1.
    // Manual page browsing is untouched until the next cursor move.
    useEffect(() => {
        if (cursor >= 1) setPage(pageOf(cursor - 1));
    }, [cursor]);
    useEffect(() => {
        timelineRef.current
            ?.querySelector('[data-active="1"]')
            ?.scrollIntoView({ block: "nearest" });
    }, [cursor, page, pageItems]);

    // A run only has something to replay once its recorded event log is loaded.
    // The backend returns RUN_NOT_FOUND (open error) when the log is empty — in
    // local dev the log is in-memory and clears on restart/hot-reload — so
    // surface that plainly instead of leaving a dead "0 / 0" scrubber.
    const isOpening = openReplay.isPending;
    const hasLog = total > 0;

    return (
        <div className="h-full flex flex-col overflow-hidden" data-testid="replay-scrubber">
            <div className="flex items-center justify-between px-4 py-2 border-b">
                <span className="text-sm font-semibold">Replay debugger</span>
                <Button variant="ghost" size="sm" onClick={onClose}>Close replay</Button>
            </div>

            {isOpening && (
                <div
                    className="flex flex-1 items-center justify-center p-6 text-xs text-muted-foreground"
                    data-testid="replay-loading"
                >
                    Loading replay…
                </div>
            )}

            {!isOpening && !hasLog && (
                <div
                    className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center text-xs text-muted-foreground"
                    data-testid="replay-empty"
                >
                    <XCircle className="size-6 text-muted-foreground/60" />
                    <div className="text-sm font-semibold text-foreground">Nothing to replay</div>
                    <p>
                        This run has no recorded event log. In local dev the event log is in-memory
                        and is cleared on backend restart or hot-reload — re-run the workflow to
                        capture a fresh, replayable log.
                    </p>
                </div>
            )}

            {hasLog && (
            <>
            <div className="border-b">
                <div
                    ref={timelineRef}
                    className="flex flex-wrap content-start gap-1 px-4 py-2 max-h-56 overflow-y-auto"
                    data-testid="replay-timeline"
                >
                    {pageItems.length === 0 && timelinePage.isFetching && (
                        <span className="text-xs text-muted-foreground" data-testid="replay-timeline-loading">
                            Loading events…
                        </span>
                    )}
                    {pageItems.map((entry) => (
                        <button
                            key={entry.index}
                            type="button"
                            data-node={entry.node_id ?? ""}
                            data-error={entry.is_error ? "1" : "0"}
                            data-active={entry.index === cursor ? "1" : "0"}
                            onClick={() => goTo(entry.index)}
                            className={cn(
                                "px-2 py-0.5 rounded text-xs border",
                                entry.index === cursor
                                    ? "bg-primary text-primary-foreground border-primary"
                                    : entry.is_error
                                        ? "bg-red-500/15 text-red-600 border-red-500/40"
                                        : "bg-muted border-border",
                            )}
                        >
                            {timelineLabel(entry)}
                        </button>
                    ))}
                </div>
                {pages > 1 && (
                    // DS structural parts (nav/ul/li) + Button controls — the DS
                    // PaginationPrevious/Next are anchors with no native disabled,
                    // which this action-style pager needs.
                    <Pagination className="mx-0 justify-between px-4 pb-2" data-testid="replay-pager">
                        <PaginationContent className="w-full justify-between">
                            <PaginationItem>
                                <Button
                                    variant="outline" size="icon" aria-label="previous events page"
                                    data-testid="replay-page-prev"
                                    disabled={page === 0} onClick={() => setPage(page - 1)}
                                >
                                    <ChevronLeft className="size-4" />
                                </Button>
                            </PaginationItem>
                            <PaginationItem>
                                <span className="text-xs text-muted-foreground" data-testid="replay-page-label">
                                    Page {page + 1} / {pages} · events {page * TIMELINE_PAGE_SIZE + 1}–{Math.min((page + 1) * TIMELINE_PAGE_SIZE, total)} of {total}
                                </span>
                            </PaginationItem>
                            <PaginationItem>
                                <Button
                                    variant="outline" size="icon" aria-label="next events page"
                                    data-testid="replay-page-next"
                                    disabled={page >= pages - 1} onClick={() => setPage(page + 1)}
                                >
                                    <ChevronRight className="size-4" />
                                </Button>
                            </PaginationItem>
                        </PaginationContent>
                    </Pagination>
                )}
            </div>

            <div className="flex items-center gap-2 px-4 py-2 border-b">
                <Button variant="outline" size="icon" onClick={() => goTo(0)} aria-label="first">
                    <SkipBack className="size-4" />
                </Button>
                <Button variant="outline" size="icon" onClick={() => goTo(cursor - 1)} aria-label="prev">
                    <ChevronLeft className="size-4" />
                </Button>
                <input
                    type="range" min={0} max={total} value={cursor}
                    onChange={(e) => goTo(Number(e.target.value))}
                    className="flex-1" aria-label="scrubber"
                />
                <Button variant="outline" size="icon" onClick={() => goTo(cursor + 1)} aria-label="next">
                    <ChevronRight className="size-4" />
                </Button>
                <Button variant="outline" size="icon" onClick={() => goTo(total)} aria-label="last">
                    <SkipForward className="size-4" />
                </Button>
                <span className="text-xs text-muted-foreground whitespace-nowrap" data-testid="replay-cursor">
                    {cursor} / {total}
                </span>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-3 text-xs">
                {state && (
                    <>
                        <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-muted-foreground">node @ cursor:</span>
                            <span className="font-semibold" data-testid="replay-node">{state.node_id ?? "—"}</span>
                            {state.node_id && state.node_status[state.node_id] && (
                                <span className="text-muted-foreground" data-testid="replay-status">
                                    [{state.node_status[state.node_id]}]
                                </span>
                            )}
                            {branchPorts.length > 0 && (
                                <span className="text-blue-600" data-testid="replay-branch">
                                    branch → {branchPorts.join(", ")}
                                </span>
                            )}
                        </div>

                        {state.node_error && (
                            <div
                                className="flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 p-2 text-red-700"
                                data-testid="replay-error"
                            >
                                <XCircle className="size-4 mt-0.5 shrink-0" />
                                <div>
                                    <div className="font-semibold">Problem in node ‘{state.node_id}’</div>
                                    <div>{state.node_error.error_message ?? state.node_error.error_code}</div>
                                </div>
                            </div>
                        )}

                        {state.redacted_keys.length > 0 && (
                            <div className="flex items-center gap-1 text-amber-600" data-testid="replay-redacted">
                                <ShieldAlert className="size-3" />
                                <span>redacted: {state.redacted_keys.join(", ")}</span>
                            </div>
                        )}

                        {/* Input | Output toggle (n8n-style) */}
                        <div className="flex gap-1">
                            <Button
                                variant={view === "input" ? "default" : "outline"} size="sm"
                                data-testid="replay-view-input" onClick={() => setView("input")}
                            >Input</Button>
                            <Button
                                variant={view === "output" ? "default" : "outline"} size="sm"
                                data-testid="replay-view-output" onClick={() => setView("output")}
                            >Output</Button>
                        </div>
                        {view === "output" ? (
                            <Section title="Output (redacted)" testid="replay-io" value={{
                                output: state.node_io.output,
                                state_updates: state.node_io.state_updates,
                                taken_edge_ports: state.node_io.taken_edge_ports,
                            }} />
                        ) : (
                            <Section title="Input (redacted)" testid="replay-io" value={state.node_io.input} />
                        )}

                        <Section title="Scope (redacted)" value={state.scope_chain} testid="replay-scope" />
                        {Object.keys(state.loop_ctx).length > 0 && (
                            <Section title="Loop context" value={state.loop_ctx} />
                        )}
                        <div className="text-muted-foreground">
                            completed: {state.completed_nodes.join(", ") || "—"}
                            {state.replay_complete && (
                                <span className="ml-2 text-emerald-600">✓ replay complete</span>
                            )}
                        </div>
                    </>
                )}
            </div>
            </>
            )}
        </div>
    );
};

export default ReplayScrubber;
