import CopyUtils from "@/components/utils/copy-utils";
import { WORKFLOW_EXECUTION_STATUS_ENUM, WORKFLOW_EXECUTION_STATUS_TEXT } from "@/constants/workflows";
import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { useQueryClient } from "@ldc/tanstack-query";
import { differenceInMilliseconds } from "date-fns";
import { ChevronDown, ChevronRight, Layers, SquareArrowOutUpRight } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useRunChildren, useRunRating, useRunRatings, useRateRun, workflowKey } from "../../hooks/apis/workflows";
import { useUIPanelStore } from "../../stores/ui-panel-stores";
import type { IEnvironment, IRunChild, ITriggeredBy } from "../../types/execution";
import type { IRunRatingView } from "../../types/rating";
import { formatDuration } from "../../utils/workflow-log-utils";
import { RatingChip } from "../common/rating-chip";
import { RelativeTime } from "../common/relative-time";
import { StarButton } from "../common/star-button";

export const STATUS_BADGE_CLASS: Record<string, string> = {
    [WORKFLOW_EXECUTION_STATUS_ENUM.COMPLETED]: "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
    [WORKFLOW_EXECUTION_STATUS_ENUM.FAILED]: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
    [WORKFLOW_EXECUTION_STATUS_ENUM.CREATED]: "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30",
    [WORKFLOW_EXECUTION_STATUS_ENUM.RUNNING]: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30",
    [WORKFLOW_EXECUTION_STATUS_ENUM.CANCELLED]: "bg-muted text-muted-foreground border-border",
    [WORKFLOW_EXECUTION_STATUS_ENUM.PENDING]: "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30",
    [WORKFLOW_EXECUTION_STATUS_ENUM.PAUSED]: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30",
};

// Normalized run shape rendered by a row — a root run or a sub-run both map to this.
export interface IRunRow {
    id: string;
    workflow_name?: string | null;
    workflow_id?: string;
    status: string;
    started_at: string | null;
    completed_at: string | null;
    parent_node_id?: string;
    // SA-2741: a run's own lineage — used to tell a sub-run from a root even when
    // it is rendered at depth 0 (e.g. the SA-2704 run-id filter pins a sub-run as
    // the list row). Rating stays top-level only, so a sub-run never gets a control.
    parent_run_id?: string | null;
    root_run_id?: string | null;
    // SA-2650: live progress (only for RUNNING rows; null otherwise).
    progress_pct?: number | null;
    completed_nodes?: number | null;
    total_nodes?: number | null;
    // SA-2558: who triggered this run (chip on the row).
    triggered_by?: ITriggeredBy | null;
    // SA-2719: which environment this run used (chip on the row).
    environment?: IEnvironment | null;
}

const childToRow = (c: IRunChild): IRunRow => ({
    id: c.child_run_id,
    workflow_id: c.workflow_id,
    status: c.status,
    started_at: c.started_at,
    completed_at: c.completed_at,
    parent_node_id: c.parent_node_id,
    progress_pct: c.progress_pct,
    completed_nodes: c.completed_nodes,
    total_nodes: c.total_nodes,
    triggered_by: c.triggered_by,
    environment: c.environment,
});

// SA-2558: the "triggered by" chip — one per run row, styled like the status
// chip (rounded-full, 12px). Plain text only: "by <name>" for a person, "by
// System" for automated / M2M / schedule runs. No leading icon and no initials
// avatar (an abbreviation like "CP" for "Casper Tran" read as confusing); we
// don't distinguish schedule from system yet. Render-only.
const TriggeredByChip = ({ triggeredBy, label: byWord }: { triggeredBy: ITriggeredBy; label: string }) => {
    const text = triggeredBy.label || "System";
    return (
        <span
            className={cn(
                "inline-flex max-w-[12rem] shrink-0 items-center truncate rounded-full border px-2 py-0.5 text-xs font-medium",
                "border-border bg-muted text-muted-foreground"
            )}
            title={`${byWord} ${text}`}
            aria-label={`${byWord} ${text}`}
        >
            <span className="text-muted-foreground/70">{byWord}&nbsp;</span>
            {text}
        </span>
    );
};

// SA-2719: the environment chip — which Environment Collection a run resolved
// {{$env.KEY}} from. A neutral chip + a tier-coloured dot + a layers icon,
// styled like the triggered-by chip. Production carries a subtle amber tint so
// running against prod is noticeable at a glance. A run that resolved no
// environment (unbound flow / no {{$env}} / historical row) shows a neutral
// dashed "No environment" chip — never blank. Each row (parent or subflow)
// renders its OWN environment; the FE never inherits/fakes it. Render-only.
// SA-2719: ALL env chips share ONE colour (violet) — distinct from the neutral
// triggered-by chip and from the status / progress palette (green/red/yellow/
// blue), and deliberately NOT yellow (which clashed with Running / progress).
// The "section / name" text inside tells environments apart; no per-tier colour.
// The "No environment" case keeps the neutral dashed style.
const EnvironmentChip = ({ environment }: { environment?: IEnvironment | null }) => {
    if (!environment) {
        return (
            <span
                className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-dashed border-border bg-transparent px-2 py-0.5 text-xs font-medium text-muted-foreground"
                title="No environment"
                aria-label="Environment: none"
            >
                No environment
            </span>
        );
    }
    const { name, section } = environment;
    const label = section ? `${section} / ${name}` : name || "Environment";
    return (
        <span
            className="inline-flex max-w-[16rem] shrink-0 items-center gap-1.5 truncate rounded-full border border-violet-500/30 bg-violet-500/15 px-2 py-0.5 text-xs font-medium text-violet-700 dark:text-violet-400"
            title={`Environment: ${label}`}
            aria-label={`Environment: ${label}`}
        >
            <Layers className="size-3 shrink-0" />
            {section ? <span className="shrink-0 opacity-70">{section}&nbsp;/&nbsp;</span> : null}
            <span className="truncate">{name || "Environment"}</span>
        </span>
    );
};

// SA-2650: statuses that show a progress chip, with a colour that matches the
// status pill so chip + pill read as one unit. RUNNING = live progress
// (yellow); FAILED = how far it got before stopping (red).
const PROGRESS_CHIP_STYLE: Record<string, { chip: string; bar: string; track: string }> = {
    [WORKFLOW_EXECUTION_STATUS_ENUM.RUNNING]: {
        chip: STATUS_BADGE_CLASS[WORKFLOW_EXECUTION_STATUS_ENUM.RUNNING]!,
        bar: "bg-yellow-500",
        track: "bg-yellow-500/25",
    },
    [WORKFLOW_EXECUTION_STATUS_ENUM.FAILED]: {
        chip: STATUS_BADGE_CLASS[WORKFLOW_EXECUTION_STATUS_ENUM.FAILED]!,
        bar: "bg-red-500",
        track: "bg-red-500/25",
    },
};

// The progress chip. At 100% the bar is dropped (a running row flips to
// Completed on the next tick). Backend-computed; render-only here.
const RunProgressChip = ({
    status,
    pct,
    completed,
    total,
}: {
    status: string;
    pct: number;
    completed?: number | null;
    total?: number | null;
}) => {
    const style = PROGRESS_CHIP_STYLE[status];
    if (!style) return null;
    // SA-2650: a RUNNING chip animates (pulsing ring + bar shimmer) so it reads
    // as actively working between the ~4s polls; FAILED is terminal, so static.
    const isRunning = status === WORKFLOW_EXECUTION_STATUS_ENUM.RUNNING;
    const clamped = Math.max(0, Math.min(100, Math.round(pct)));
    const hasNodes = completed != null && total != null;
    const verb = status === WORKFLOW_EXECUTION_STATUS_ENUM.FAILED ? "reached" : "complete";
    const label = hasNodes
        ? `${clamped}% ${verb}, ${completed} of ${total} nodes`
        : `${clamped}% ${verb}`;
    return (
        <span
            role="progressbar"
            aria-valuenow={clamped}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={label}
            title={hasNodes ? `${completed}/${total} nodes` : undefined}
            className={cn(
                "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-semibold",
                style.chip,
                isRunning && "chip-running"
            )}
        >
            <span>{clamped}%</span>
            {clamped < 100 ? (
                <span className={cn("h-1 w-10 overflow-hidden rounded-full", style.track)}>
                    <span
                        className={cn("block h-full rounded-full transition-all", style.bar, isRunning && "chip-bar-running")}
                        style={{ width: `${clamped}%` }}
                    />
                </span>
            ) : null}
        </span>
    );
};

// ponytail: cap recursion — runs form a DAG, but guard against a malformed cycle.
const MAX_DEPTH = 8;

interface ExecutionRunRowProps {
    run: IRunRow;
    depth?: number;
    // SA-2704: when the list is pinned to a single run (run-id filter), the
    // matched row opens auto-expanded so its sub-runs are visible immediately.
    defaultExpanded?: boolean;
    // SA-2825: the batched rating map for the visible top-level rows. When `batched`
    // is set, this row reads its slice from the shared map instead of firing its own
    // /rating/by-run call (kills the per-row N+1). Sub-runs render without `batched`
    // and fall back to a self-fetch.
    ratingsById?: IRunRatingsMap;
    batched?: boolean;
}

type IRunRatingsMap = Record<string, IRunRatingView>;

// SA-2704: build the shareable deep link to a run — same-origin path + the
// exec-history tab + the runId filter param. Opening it lands directly on the run.
const buildRunDeepLink = (runId: string): string => {
    if (typeof window === "undefined") return "";
    const { origin, pathname } = window.location;
    return `${origin}${pathname}?tab=execution-history&runId=${encodeURIComponent(runId)}`;
};

const ExecutionRunRow = ({ run, depth = 0, defaultExpanded = false, ratingsById, batched = false }: ExecutionRunRowProps) => {
    const { t } = useLanguage();
    const selectedRunId = useUIPanelStore((s) => s.selectedRunId);
    const setSelectedRunId = useUIPanelStore((s) => s.setSelectedRunId);
    const setIsLogSidebarOpen = useUIPanelStore((s) => s.setIsLogSidebarOpen);

    const [expanded, setExpanded] = useState(defaultExpanded);
    const canExpand = depth < MAX_DEPTH;

    // Only fetch a run's sub-runs once it's actually expanded.
    const { data, isLoading } = useRunChildren(run.id, { enabled: expanded && canExpand });
    const children = data?.data.children ?? [];

    // SA-2825: batch THIS parent's direct children into one /rating/by-run call
    // (instead of one per child row) once expanded. Each expanded level fires a
    // single call; child rows read their slice via `batched` below.
    const childIds = useMemo(() => children.map((c) => c.child_run_id), [children]);
    const { data: childRatings } = useRunRatings(childIds, { enabled: expanded && canExpand && childIds.length > 0 });

    // SA-2876: this run's own stars + its workflow total + whether the viewer
    // already starred it (a star is permanent → the button locks).
    // SA-2825: a batched row reads its slice from the list-level map (one
    // /rating/by-run for all visible rows) and NEVER self-fetches — gating on
    // `batched` (not on the data being present) avoids a first-render race where
    // every row would fire its own call before the batch resolves. Sub-runs
    // (rendered without `batched`) self-fetch, since they aren't in the page batch.
    const queryClient = useQueryClient();
    const { data: fetchedRating } = useRunRating(run.id, { enabled: !!run.id && !batched });
    const rating = batched ? ratingsById?.[run.id] : fetchedRating;
    const [starredOptimistic, setStarredOptimistic] = useState(false);
    const starred = starredOptimistic || !!rating?.my_starred;
    // Optimistic +1 until the refetch confirms the server's count.
    const bump = starredOptimistic && !rating?.my_starred ? 1 : 0;
    const { mutate: starRun, isPending: isStarring } = useRateRun({
        onSuccess: () => {
            // Reconcile every star chip (this row + the Workflows tab) from the
            // server; the socket broadcast refreshes other open clients (SA-2670).
            void queryClient.invalidateQueries({ queryKey: workflowKey.ratings() });
            toast.success(t("star_saved"));
        },
        onError: () => {
            setStarredOptimistic(false);
            toast.error(t("star_failed"));
        },
    });
    const handleStar = () => {
        setStarredOptimistic(true);
        starRun({ runId: run.id });
    };

    const status = run.status?.toLowerCase() || "";
    // SA-2741: a sub-run is never rateable — not just when nested (depth>0), but
    // also when it is the pinned list row (run-id filter renders it at depth 0).
    // Derive it from the run's own lineage so the rate control (and the 409 it
    // would trigger) never appears for a sub-run.
    const isSubRun =
        depth > 0 ||
        !!run.parent_run_id ||
        (!!run.root_run_id && run.root_run_id !== run.id);
    const title = run.workflow_name?.trim() || (isSubRun ? t("sub_run") : run.workflow_id) || "—";

    const openLog = () => {
        setSelectedRunId(run.id);
        setIsLogSidebarOpen(true);
    };

    const duration =
        run.completed_at && run.started_at
            ? formatDuration(differenceInMilliseconds(new Date(run.completed_at), new Date(run.started_at)))
            : t("running");

    return (
        <div>
            <div
                role="button"
                tabIndex={0}
                onClick={openLog}
                onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === " ") {
                        ev.preventDefault();
                        openLog();
                    }
                }}
                className={cn(
                    "w-full cursor-pointer rounded-lg border bg-card p-3 text-left transition-colors hover:bg-muted/50",
                    "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary",
                    selectedRunId === run.id ? "border-primary ring-1 ring-primary" : "border-border"
                )}
            >
                <div className="flex items-start gap-2">
                    {canExpand ? (
                        <button
                            type="button"
                            aria-label={expanded ? t("collapse_sub_runs") : t("expand_sub_runs")}
                            aria-expanded={expanded}
                            onClick={(ev) => {
                                ev.stopPropagation();
                                setExpanded((v) => !v);
                            }}
                            className="mt-0.5 shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                        >
                            {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                        </button>
                    ) : (
                        <span className="w-5 shrink-0" />
                    )}

                    <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                            <span className="truncate font-medium">{title}</span>
                            <Badge
                                variant="outline"
                                className={cn("shrink-0 border text-xs capitalize", STATUS_BADGE_CLASS[status])}
                            >
                                {t(WORKFLOW_EXECUTION_STATUS_TEXT[status] ?? status)}
                            </Badge>
                            {run.progress_pct != null && PROGRESS_CHIP_STYLE[status] ? (
                                <RunProgressChip
                                    status={status}
                                    pct={run.progress_pct}
                                    completed={run.completed_nodes}
                                    total={run.total_nodes}
                                />
                            ) : null}
                            {run.triggered_by ? (
                                <TriggeredByChip triggeredBy={run.triggered_by} label={t("triggered_by_word")} />
                            ) : null}
                            {/* SA-2719: which environment this run resolved {{$env}} from.
                                Always shown (neutral "No environment" chip when none) so the
                                column is never blank; each row shows its own environment. */}
                            <EnvironmentChip environment={run.environment} />
                            {/* SA-2876: the run's WORKFLOW total stars (over all its runs).
                                Hover = who starred the workflow's runs. */}
                            {rating && rating.workflow_total + bump > 0 ? (
                                <RatingChip workflowId={rating.workflow_id} count={rating.workflow_total + bump} />
                            ) : null}
                            {isSubRun && run.parent_node_id ? (
                                <span className="truncate text-xs text-muted-foreground">
                                    {t("via_node", { node: run.parent_node_id })}
                                </span>
                            ) : null}
                        </div>

                        <div className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
                            <span className="truncate font-mono">{run.id}</span>
                            <span onClick={(ev) => ev.stopPropagation()}>
                                <CopyUtils content={run.id ?? ""} title={t("copy_run_id")} />
                            </span>
                            {/* SA-2704: copy a shareable deep link to this run (filters
                                the exec-history list to it). Sibling of the copy-id button. */}
                            <span onClick={(ev) => ev.stopPropagation()}>
                                <CopyUtils
                                    content={buildRunDeepLink(run.id ?? "")}
                                    icon="link"
                                    title={t("copy_run_link")}
                                />
                            </span>
                            {/* SA-2848: open the workflow that produced this run in a new tab.
                                A real link (Ctrl/middle-click work); each row — incl. sub-runs —
                                uses its OWN workflow_id. stopPropagation keeps the log sidebar closed. */}
                            {run.workflow_id ? (
                                <Link
                                    to={`/workflow/${run.workflow_id}`}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    onClick={(ev) => ev.stopPropagation()}
                                    onKeyDown={(ev) => ev.stopPropagation()}
                                    title={t("open_workflow_new_tab")}
                                    aria-label={t("open_workflow_new_tab")}
                                    className="ml-2 inline-flex items-center text-muted-foreground hover:text-primary"
                                >
                                    <SquareArrowOutUpRight className="size-3" />
                                </Link>
                            ) : null}
                        </div>

                        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            <RelativeTime value={run.started_at} />
                            <span>·</span>
                            <span className="whitespace-nowrap">{duration}</span>
                            {/* SA-2876: Star on the TOP-LEVEL run only — a star adds to every
                                flow in that run; sub-runs show the totals, no control. */}
                            {!isSubRun ? (
                                <>
                                    <span>·</span>
                                    <span onClick={(ev) => ev.stopPropagation()}>
                                        <StarButton
                                            starred={starred}
                                            count={(rating?.count ?? 0) + bump}
                                            disabled={isStarring}
                                            onStar={handleStar}
                                            labels={{
                                                star: t("star_run"),
                                                starred: t("starred_run"),
                                                hint: t("star_run_hint"),
                                                lockedHint: t("starred_run_hint"),
                                            }}
                                        />
                                    </span>
                                </>
                            ) : null}
                        </div>
                    </div>
                </div>
            </div>

            {expanded && canExpand ? (
                <div className="ml-3 mt-1 space-y-1 border-l border-border pl-3">
                    {isLoading ? (
                        <div className="py-2 text-xs text-muted-foreground">{t("loading")}…</div>
                    ) : children.length === 0 ? (
                        <div className="py-2 text-xs text-muted-foreground">{t("no_sub_runs")}</div>
                    ) : (
                        children.map((c) => (
                            <ExecutionRunRow key={c.child_run_id} run={childToRow(c)} depth={depth + 1} ratingsById={childRatings} batched />
                        ))
                    )}
                </div>
            ) : null}
        </div>
    );
};

export default ExecutionRunRow;
