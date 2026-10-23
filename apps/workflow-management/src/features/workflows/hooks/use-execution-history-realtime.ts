import { pushGatewaySocket } from "@/lib/socket";
import { useQueryClient } from "@ldc/tanstack-query";
import { useEffect } from "react";
import type {
    IRunChild,
    IRunChildrenResponse,
    IWorkflowExecutionHistory,
    IWorkflowExecutionHistoryResponse,
    IWorkflowExecutionParams,
} from "../types/execution";
import { SocketEvent } from "../types/socket-event";
import { getSocketEventName, getSocketEventPayload, normalizeSocketPayload } from "../utils/socket-data-chunk";
import { workflowKey } from "./apis/workflows";

// The "all runs" broadcast feed (same push-gateway feed that colours nodes live
// in the editor). Joined ONLY while the Execution-History tab is active.
const RUNS_FEED_ROOM = "runs:all";
const LIST_KEY_PREFIX = workflowKey.getAllWorkflowExecutions({}).slice(0, -1);
// Prefix matching EVERY runChildren(<id>) query, so a resync can refresh the
// sub-run rows of any expanded parent (not just the top-level list).
const CHILDREN_KEY_PREFIX = workflowKey.runChildren("").slice(0, -1);
// SA-2741: prefix matching every rating query (workflow aggregates, per-run, raters).
const RATING_KEY_PREFIX = workflowKey.ratings();
const DEFAULT_PAGE_SIZE = 20;

// Node terminal events that advance progress (SA-2650 counts completed + skipped,
// EXCLUDES failed — so bump on completed/cancelled, not on failed).
const PROGRESS_NODE_EVENTS = new Set<string>([SocketEvent.NodeCompleted, SocketEvent.NodeCancelled]);
const CHILD_EVENTS = new Set<string>([
    SocketEvent.ChildRunStarted,
    SocketEvent.ChildRunCompleted,
    SocketEvent.ChildRunFailed,
    SocketEvent.ChildRunCancelled,
]);
const TERMINAL_STATUS: Record<string, string> = {
    completed: "COMPLETED",
    failed: "FAILED",
    cancelled: "CANCELLED",
};

const computePct = (completed: number, total: number | null | undefined): number | null =>
    total && total > 0 ? Math.min(100, Math.max(0, Math.round((completed / total) * 100))) : null;

/**
 * SA-2684 (builds on SA-2670): drive the Execution History list purely from the
 * realtime socket — no `/runs` refetch, at any scale.
 *
 * - `run.status started` for a run we don't have yet → INSERT its row at the top
 *   of the (unfiltered) first page straight from the event payload (BE stamps
 *   `workflow_name` + `total_nodes` on the started event, SA-2684). No fetch.
 * - `node.completed` / `node.cancelled` for a visible run → bump `completed_nodes`
 *   and recompute `progress_pct` in place.
 * - `run.status completed/failed/cancelled` for a visible run → flip status,
 *   clear the chip.
 * - Events for runs NOT on the current page → IGNORED (they aren't shown, so a
 *   refetch would be pure waste — this is what makes 1000 concurrent runs cost
 *   zero `/runs`).
 *
 * The only refetch is a single resync on socket (re)connect, to recover any
 * events missed while the tab/socket was away. Idle feed → zero requests.
 */
export const useExecutionHistoryRealtime = (active: boolean) => {
    const queryClient = useQueryClient();

    useEffect(() => {
        if (!active) return;
        const socket = pushGatewaySocket.connect("/v1/pushgateway");

        const resync = () => {
            // Refresh BOTH the top-level list AND every expanded parent's
            // sub-run rows. Without the children resync, a missed child
            // completion event leaves an expanded sub-run stuck showing its
            // last live status/% (e.g. "Running 40%") long after it finished —
            // the exact stale-nested-row symptom seen on a synchronous
            // (wait_for_completion) parent that had already completed.
            void queryClient.invalidateQueries({ queryKey: LIST_KEY_PREFIX });
            void queryClient.invalidateQueries({ queryKey: CHILDREN_KEY_PREFIX });
        };

        const onConnect = () => {
            socket.emit("wait", { key: RUNS_FEED_ROOM });
            // Resync once — we may have missed events while disconnected.
            resync();
        };
        if (socket.connected) socket.emit("wait", { key: RUNS_FEED_ROOM });
        socket.on("connect", onConnect);
        socket.on("reconnect", onConnect);

        // Resync when the window/tab regains focus — a safety net for any events
        // missed while the socket hiccuped or the tab was in the background. This
        // complements react-query's refetchOnWindowFocus (which keys off
        // visibilitychange): the 'focus' event also covers OS alt-tab back to the
        // browser. Cheap — fires only on refocus, never polls.
        const onWindowFocus = () => {
            resync();
        };
        window.addEventListener("focus", onWindowFocus);

        // Patch an existing row across every cached list page; true if present.
        const patchRun = (
            runId: string,
            patch: (row: IWorkflowExecutionHistory) => Partial<IWorkflowExecutionHistory> | null,
        ): boolean => {
            let found = false;
            queryClient.setQueriesData<IWorkflowExecutionHistoryResponse>(
                { queryKey: LIST_KEY_PREFIX },
                (old) => {
                    if (!old) return old;
                    const items = old.data?.items;
                    if (!Array.isArray(items)) return old;
                    let changed = false;
                    const next = items.map((row) => {
                        if (row.id !== runId) return row;
                        found = true;
                        const upd = patch(row);
                        if (!upd) return row;
                        changed = true;
                        return { ...row, ...upd };
                    });
                    return changed ? { ...old, data: { ...old.data, items: next } } : old;
                },
            );
            return found;
        };

        // Insert a brand-new run at the top of the unfiltered first page(s), from
        // the event payload — no fetch. Skipped for filtered / non-first pages.
        const insertRun = (row: IWorkflowExecutionHistory) => {
            for (const query of queryClient.getQueryCache().findAll({ queryKey: LIST_KEY_PREFIX })) {
                const params = query.queryKey[query.queryKey.length - 1] as IWorkflowExecutionParams | undefined;
                if (params && (params.$skip ?? 0) !== 0) continue; // first page only
                if (params && params.$filter) continue; // unfiltered only
                const pageSize = params?.$top ?? DEFAULT_PAGE_SIZE;
                queryClient.setQueryData<IWorkflowExecutionHistoryResponse>(query.queryKey, (old) => {
                    if (!old?.data || !Array.isArray(old.data.items)) return old;
                    if (old.data.items.some((r) => r.id === row.id)) return old;
                    const items = [row, ...old.data.items].slice(0, pageSize);
                    return { ...old, data: { ...old.data, items, total: (old.data.total ?? 0) + 1 } };
                });
            }
        };

        // Patch a sub-run row (by child_run_id) in EVERY runChildren cache. Works
        // at ANY nesting depth — we search all parent caches, so we don't need the
        // child's immediate parent id (which node/run.status events don't carry).
        const patchChild = (childRunId: string, patch: (c: IRunChild) => Partial<IRunChild> | null) => {
            for (const query of queryClient.getQueryCache().findAll({ queryKey: CHILDREN_KEY_PREFIX })) {
                queryClient.setQueryData<IRunChildrenResponse>(query.queryKey, (old) => {
                    const kids = old?.data?.children;
                    if (!Array.isArray(kids)) return old;
                    let changed = false;
                    const children = kids.map((c) => {
                        if (c.child_run_id !== childRunId) return c;
                        const upd = patch(c);
                        if (!upd) return c;
                        changed = true;
                        return { ...c, ...upd };
                    });
                    return changed && old ? { ...old, data: { ...old.data, children } } : old;
                });
            }
        };

        // Insert a freshly-spawned sub-run into its parent's runChildren cache,
        // keyed by the IMMEDIATE parent so grandchildren nest under the right node
        // at any depth. No-op if the parent isn't expanded (nothing cached) — the
        // fetch on expand, or a resync, picks it up. No /children refetch.
        const insertChild = (parentRunId: string, row: IRunChild) => {
            queryClient.setQueryData<IRunChildrenResponse>(workflowKey.runChildren(parentRunId), (old) => {
                const kids = old?.data?.children;
                if (!Array.isArray(kids) || !old) return old;
                if (kids.some((c) => c.child_run_id === row.child_run_id)) return old;
                return { ...old, data: { ...old.data, children: [...kids, row] } };
            });
        };

        const handleDataChunk = (message: unknown) => {
            const normalized = normalizeSocketPayload(message);
            if (!normalized) return;
            const event = getSocketEventName(normalized);
            const payload = getSocketEventPayload(normalized);
            const runId = String((payload.run_id as string) || "");

            // SA-2741: a rating changed anywhere (this client, another user, a
            // direct API call, or a bot) → refresh every rating chip. Ratings
            // aren't part of the run-row lifecycle, so this is a plain invalidate
            // (cheap — only the currently-visible rating queries refetch).
            if (event === SocketEvent.RatingUpdated) {
                void queryClient.invalidateQueries({ queryKey: RATING_KEY_PREFIX });
                return;
            }

            // Subflow child lifecycle → PUSH into the parent's runChildren cache
            // from the payload (SA-2684 parity with the list). No /children
            // refetch: ChildRunStarted inserts the nested row, ChildRun{Completed,
            // Failed,Cancelled} flips its status. Idle feed → zero requests.
            if (CHILD_EVENTS.has(event)) {
                const childRunId = String((payload.child_run_id as string) || "");
                if (!childRunId) return;
                if (event === SocketEvent.ChildRunStarted) {
                    insertChild(String((payload.parent_run_id as string) || ""), {
                        child_run_id: childRunId,
                        workflow_id: String((payload.child_workflow_id as string) || ""),
                        parent_node_id: String((payload.parent_node_id as string) || ""),
                        status: "RUNNING",
                        started_at: typeof payload._timestamp === "string" ? (payload._timestamp as string) : new Date().toISOString(),
                        completed_at: null,
                        error: null,
                        progress_pct: 0,
                        completed_nodes: 0,
                        total_nodes: null,
                        triggered_by: null,
                    });
                } else {
                    const st = event === SocketEvent.ChildRunCompleted ? "COMPLETED"
                        : event === SocketEvent.ChildRunFailed ? "FAILED" : "CANCELLED";
                    const endedAt = typeof payload._timestamp === "string" ? (payload._timestamp as string) : new Date().toISOString();
                    patchChild(childRunId, (c) => ({ status: st, progress_pct: null, completed_at: c.completed_at ?? endedAt }));
                }
                return;
            }
            if (!runId) return;

            // Is this event about a SUBFLOW child run (not a root)? The BE stamps
            // every event with depth (0 for a root) and root_run_id (== run_id for
            // a root, the top root's id for a child) — see orchestration_engine
            // emit_run_status. A child must NEVER become a top-level row: the list
            // is roots-only, children live nested under their parent via the
            // runChildren query. This is what keeps the display a TREE instead of
            // showing the parent + each subflow as separate top-level flows.
            const rootRunId = String((payload.root_run_id as string) || "");
            const isChild = Number(payload.depth) > 0 || (!!rootRunId && rootRunId !== runId);

            if (event === SocketEvent.RunStatus) {
                const status = String((payload.status as string) || "").toLowerCase();
                if (status === "started") {
                    // A child's own run.status=started must not be inserted at the
                    // top level. Its nested row is added when the ChildRunStarted
                    // event refreshes the parent's runChildren query (CHILD_EVENTS
                    // above), which carries the correct immediate parent id.
                    if (isChild) return;
                    if (patchRun(runId, () => ({ status: "RUNNING" }))) return; // already known
                    const total = Number(payload.total_nodes) || null;
                    insertRun({
                        id: runId,
                        workflow_id: String((payload.workflow_id as string) || ""),
                        workflow_name: String((payload.workflow_name as string) || ""),
                        status: "RUNNING",
                        started_at:
                            typeof payload._timestamp === "string"
                                ? (payload._timestamp as string)
                                : new Date().toISOString(),
                        completed_at: null,
                        error: null,
                        is_test: false,
                        tenant_id: "",
                        progress_pct: total ? 0 : null,
                        completed_nodes: 0,
                        total_nodes: total,
                        triggered_by: null,
                    });
                } else if (TERMINAL_STATUS[status]) {
                    // Only touch it if it's on the page; a terminal event for an
                    // off-page run isn't shown, so there's nothing to update.
                    // Stamp completed_at so the row's duration stops reading
                    // "running…" the instant the status flips — while running it
                    // was null, and the status patch alone would leave it null,
                    // so a Completed row would still show a live-ticking duration
                    // until the next resync. A resync later replaces this with
                    // the authoritative server value.
                    const endedAt = typeof payload._timestamp === "string"
                        ? (payload._timestamp as string)
                        : new Date().toISOString();
                    // A child's own terminal run.status also lands here — patch its
                    // nested row (belt-and-suspenders with ChildRun{Completed,…};
                    // idempotent). patchRun on a child id would be a no-op (children
                    // aren't in the roots-only list).
                    if (isChild) {
                        patchChild(runId, (c) => ({
                            status: TERMINAL_STATUS[status],
                            progress_pct: null,
                            completed_at: c.completed_at ?? endedAt,
                        }));
                        return;
                    }
                    patchRun(runId, (row) => ({
                        status: TERMINAL_STATUS[status],
                        progress_pct: null,
                        completed_at: row.completed_at ?? endedAt,
                    }));
                }
                return;
            }

            if (PROGRESS_NODE_EVENTS.has(event)) {
                // A child run's node progress → PUSH the exact server % onto its
                // nested row (found by child_run_id in whatever parent cache holds
                // it, any depth). Monotonic guard like the list; no /children
                // refetch. NodeCompleted carries the run's run_progress_pct.
                if (isChild) {
                    const cp = payload as Record<string, unknown>;
                    const cPct = cp.run_progress_pct;
                    const cDone = Number(cp.run_completed_nodes);
                    const cTotal = Number(cp.run_total_nodes);
                    patchChild(runId, (c) => {
                        if ((c.status || "").toUpperCase() !== "RUNNING") return null;
                        if (typeof cPct !== "number") return null;
                        return {
                            progress_pct: Math.max(c.progress_pct ?? 0, cPct),
                            completed_nodes: Number.isFinite(cDone) ? cDone : c.completed_nodes,
                            total_nodes: Number.isFinite(cTotal) && cTotal > 0 ? cTotal : c.total_nodes,
                        };
                    });
                    return;
                }
                // SA-2684: NodeCompleted carries the run's EXACT server progress —
                // computed on the backend the SAME way the list endpoint does
                // (reachable-path denominator, branch-pruned). The FE just RENDERS
                // it, guarded so it never moves backward (out-of-order events). No
                // client-side division. IGNORE if the run isn't on the page (no
                // refetch → 0 cost at scale). Fallback to the deduped-count estimate
                // only for an old BE that doesn't stamp run_progress_pct.
                const p = payload as Record<string, unknown>;
                const evPct = p.run_progress_pct;
                const evDone = Number(p.run_completed_nodes);
                const evTotal = Number(p.run_total_nodes);
                patchRun(runId, (row) => {
                    if ((row.status || "").toUpperCase() !== "RUNNING") return null;
                    if (typeof evPct === "number") {
                        return {
                            progress_pct: Math.max(row.progress_pct ?? 0, evPct),
                            completed_nodes: Number.isFinite(evDone) ? evDone : row.completed_nodes,
                            total_nodes: Number.isFinite(evTotal) && evTotal > 0 ? evTotal : row.total_nodes,
                        };
                    }
                    const total = row.total_nodes ?? null;
                    const completed = Number.isFinite(evDone) && evDone > 0
                        ? Math.max(row.completed_nodes ?? 0, evDone)
                        : (row.completed_nodes ?? 0) + 1;
                    const capped = total ? Math.min(completed, total) : completed;
                    return { completed_nodes: capped, progress_pct: computePct(capped, total) };
                });
                return;
            }
            // node.dispatched / other events → no row change, ignore.
        };
        socket.on("data_chunk", handleDataChunk);

        return () => {
            window.removeEventListener("focus", onWindowFocus);
            socket.emit("leave", { key: RUNS_FEED_ROOM });
            socket.off("connect", onConnect);
            socket.off("reconnect", onConnect);
            socket.off("data_chunk", handleDataChunk);
        };
    }, [active, queryClient]);
};
