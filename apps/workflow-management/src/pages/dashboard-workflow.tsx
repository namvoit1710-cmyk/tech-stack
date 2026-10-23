// SA-2590 — Dashboard → Workflow. Filters (time/owner/name/status) drive the
// HANA-backed analytics API (SA-2597); deep-linkable via URL query params (U3/U9),
// empty state on no match (U6). Real DB data.
import Page from "@/components/containers/page";
import { fetchWorkflows, type WorkflowsData } from "@/features/dashboard/api";
import { EmptyState, Kpi, MiniBars, Panel, RANGES, RankedBars } from "@/features/dashboard/dashboard-kit";
import { humanizeSeconds } from "@/features/workflows/components/common/manual-work-chip";
import { cn } from "@ldc/ui";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

const STATUSES = ["all", "published", "draft"] as const;
const RANGE_VALUES = RANGES.map((r) => r.value);

// SA-2626: success health as a 4-tier traffic-light + orange scale — the
// palette common on enterprise SRE/observability dashboards (Grafana/Datadog):
//   >= 99% green (healthy) · 95-99% yellow (good) · 90-95% orange (degraded)
//   · < 90% red (critical).
const successTone = (r: number) =>
    r >= 99 ? "bg-emerald-500/15 text-emerald-600"
        : r >= 95 ? "bg-amber-500/15 text-amber-600"
            : r >= 90 ? "bg-orange-500/15 text-orange-600"
                : "bg-rose-500/15 text-rose-600";

// Legend for the Success color scale (rendered under the usage table).
const SUCCESS_LEGEND = [
    { tone: "bg-emerald-500/15 text-emerald-600", label: "≥ 99%", note: "healthy" },
    { tone: "bg-amber-500/15 text-amber-600", label: "95–99%", note: "good" },
    { tone: "bg-orange-500/15 text-orange-600", label: "90–95%", note: "degraded" },
    { tone: "bg-rose-500/15 text-rose-600", label: "< 90%", note: "critical" },
    { tone: "bg-muted text-muted-foreground", label: "—", note: "no runs" },
];

const DashboardWorkflowPage = () => {
    const [params, setParams] = useSearchParams();
    const navigate = useNavigate();

    const rawRange = params.get("range") ?? "7d";
    const range = RANGE_VALUES.includes(rawRange) ? rawRange : "7d";
    const owner = params.get("owner") ?? "";
    const q = params.get("q") ?? "";
    const rawStatus = params.get("status") ?? "all";
    const status = (STATUSES as readonly string[]).includes(rawStatus) ? rawStatus : "all";

    const [data, setData] = useState<WorkflowsData | null>(null);
    const [loading, setLoading] = useState(true);   // first load only (full skeleton)
    const [refreshing, setRefreshing] = useState(false); // paging / filter refetch (keep old data)
    const hasDataRef = useRef(false);
    const [error, setError] = useState<string | null>(null);
    // owner options come from an initial unfiltered fetch so the dropdown stays stable
    const [owners, setOwners] = useState<string[]>([]);
    const [page, setPage] = useState(1);
    const PAGE_SIZE = 10;

    const setParam = (key: string, value: string) => {
        const next = new URLSearchParams(params);
        if (value) next.set(key, value); else next.delete(key);
        setParams(next, { replace: true });
        setPage(1); // any filter change → back to page 1
    };
    const resetFilters = () => { setParams(new URLSearchParams(), { replace: true }); setPage(1); };

    useEffect(() => {
        fetchWorkflows({ range: "1y", pageSize: 100 }).then((d) => setOwners([...new Set(d.rows.map((r) => r.owner))].sort())).catch(() => {});
    }, []);

    useEffect(() => {
        let alive = true;
        // first load → full skeleton; later (paging / filter) → keep old data visible
        // so the layout doesn't collapse and the scroll position is preserved.
        if (hasDataRef.current) setRefreshing(true); else setLoading(true);
        setError(null);
        fetchWorkflows({ range, owner, q, status, page, pageSize: PAGE_SIZE })
            .then((d) => { if (alive) { setData(d); hasDataRef.current = true; } })
            .catch((e) => alive && setError(String(e.message || e)))
            .finally(() => { if (alive) { setLoading(false); setRefreshing(false); } });
        return () => { alive = false; };
    }, [range, owner, q, status, page]);

    const rows = data?.rows ?? [];

    const chips: { key: string; label: string }[] = [];
    if (range !== "7d") chips.push({ key: "range", label: `range: ${range}` });
    if (owner) chips.push({ key: "owner", label: `owner: ${owner}` });
    if (q) chips.push({ key: "q", label: `name: ${q}` });
    if (status !== "all") chips.push({ key: "status", label: `status: ${status}` });

    return (
        <Page>
            <Page.Header title="Workflow analytics" description="Usage & run health per workflow (live from HANA)" />
            <div className="flex-1 overflow-y-auto px-4 pb-8">
                <div className="rounded-xl border bg-card p-3 mb-3 flex flex-wrap items-end gap-3">
                    <Field label="Time range">
                        <select value={range} onChange={(e) => setParam("range", e.target.value === "7d" ? "" : e.target.value)} className="ctrl">
                            {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                        </select>
                    </Field>
                    <Field label="Owner">
                        <select value={owner} onChange={(e) => setParam("owner", e.target.value)} className="ctrl">
                            <option value="">All owners</option>
                            {owners.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                    </Field>
                    <Field label="Workflow name">
                        <input value={q} onChange={(e) => setParam("q", e.target.value)} placeholder="Search name…" className="ctrl" />
                    </Field>
                    <Field label="Status">
                        <select value={status} onChange={(e) => setParam("status", e.target.value === "all" ? "" : e.target.value)} className="ctrl">
                            {STATUSES.map((s) => <option key={s} value={s}>{s === "all" ? "All" : s}</option>)}
                        </select>
                    </Field>
                    <button onClick={resetFilters} className="ml-auto rounded-lg border px-3 py-1.5 text-sm hover:bg-muted">Reset</button>
                </div>

                {chips.length > 0 && (
                    <div className="flex flex-wrap gap-2 mb-3">
                        {chips.map((c) => (
                            <button key={c.key} onClick={() => setParam(c.key, "")} className="flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-xs hover:bg-muted/70">
                                {c.label} <span className="text-muted-foreground">✕</span>
                            </button>
                        ))}
                    </div>
                )}

                {loading && !data && <div className="text-sm text-muted-foreground py-10 text-center animate-pulse">Loading…</div>}
                {error && !data && <Panel title="Couldn’t load analytics" subtitle=""><EmptyState title="Analytics API unreachable" hint={error} /></Panel>}

                {data && (
                    <div className={cn("transition-opacity duration-150", refreshing && "opacity-60")}>
                        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
                            <Kpi label="Workflows matched" value={String(data.matched)} delta={`of ${data.total} total`} />
                            <Kpi label="Runs in range" value={data.runsInRange.toLocaleString()} />
                            <Kpi label="Avg runs / workflow" value={data.avgRuns.toLocaleString()} />
                            {/* SA-2778: time saved = Σ (successful in-range runs × manual work), filter-scoped */}
                            <Kpi label="Time saved" value={humanizeSeconds(data.savedSeconds ?? 0)} delta="successful runs × manual work · 1d = 8h workday" />
                        </div>

                        {rows.length === 0 ? (
                            <Panel title="No results" subtitle="">
                                <EmptyState title="No workflows match these filters" hint="Try widening the time range or clearing the owner / name filters."
                                    cta={<button onClick={resetFilters} className="mt-1 rounded-lg border px-3 py-1.5 text-sm hover:bg-muted">Reset filters</button>} />
                            </Panel>
                        ) : (
                            <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
                                <Panel title="Most-used workflows" subtitle="Ranked by run count within the current filter" className="lg:col-span-7">
                                    {data.ranked.length ? <RankedBars items={data.ranked} /> : <EmptyState title="No runs in range" />}
                                </Panel>
                                <Panel title="Usage trend" subtitle="Runs per bucket across matched workflows" className="lg:col-span-5">
                                    {data.trend.length ? <MiniBars values={data.trend} labels={data.trendLabels} /> : <EmptyState title="No trend data" />}
                                </Panel>
                                <Panel title="Workflow usage table" subtitle={`Click a row to open the flow · sorted by Runs (all executions) · ${data.matched} workflow${data.matched === 1 ? "" : "s"} · ${PAGE_SIZE} per page`} className="lg:col-span-12">
                                    <div className="overflow-x-auto">
                                        <table className="w-full text-sm">
                                            <thead>
                                                <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                                                    <th className="py-2 font-medium">Workflow</th><th className="py-2 font-medium">Owner</th>
                                                    <th className="py-2 font-medium text-right">Runs ▼</th>
                                                    <th className="py-2 font-medium text-right">Success</th><th className="py-2 font-medium text-right">Avg</th>
                                                    <th className="py-2 font-medium text-right">Last run</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {rows.map((w) => (
                                                    <tr key={w.id} className="border-t hover:bg-muted/40 cursor-pointer" onClick={() => navigate(`/workflow/${w.id}`)}>
                                                        <td className="py-2.5 font-medium">{w.name}</td>
                                                        <td className="py-2.5 text-muted-foreground">{w.owner}</td>
                                                        <td className="py-2.5 text-right tabular-nums font-medium">
                                                            {w.sub && w.sub > 0 ? (
                                                                <span className="group/runs relative inline-block cursor-help border-b border-dotted border-muted-foreground/60">
                                                                    {w.runs.toLocaleString()}
                                                                    <span className="pointer-events-none absolute right-0 bottom-full mb-1.5 z-20 hidden group-hover/runs:block whitespace-nowrap rounded-md bg-slate-900 px-2.5 py-1 text-xs font-medium text-white shadow-lg">
                                                                        was <span style={{ color: "#9ec5ff" }}>{(w.direct ?? 0).toLocaleString()} direct</span> + <span style={{ color: "#c4b5fd" }}>{w.sub.toLocaleString()} as sub-flow</span>
                                                                    </span>
                                                                </span>
                                                            ) : (
                                                                w.runs.toLocaleString()
                                                            )}
                                                        </td>
                                                        <td className="py-2.5 text-right">
                                                            {w.runs === 0
                                                                ? <span className="rounded-full px-2 py-0.5 text-xs bg-muted text-muted-foreground" title="No runs in range">—</span>
                                                                : <span className={cn("rounded-full px-2 py-0.5 text-xs", successTone(w.successRate))}>{w.successRate}%</span>}
                                                        </td>
                                                        <td className="py-2.5 text-right tabular-nums">{(w.avgMs / 1000).toFixed(1)}s</td>
                                                        <td className="py-2.5 text-right tabular-nums text-muted-foreground">{w.lastRun}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                    <div className="pt-2 text-xs text-muted-foreground">
                                        <p>Runs counts every execution — triggered directly or run as a sub-flow. Hover a Runs value to see the direct-vs-sub-flow breakdown; the column stays a single total.</p>
                                        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                                            <span className="font-medium">Success:</span>
                                            {SUCCESS_LEGEND.map((l) => (
                                                <span key={l.label} className="inline-flex items-center gap-1">
                                                    <span className={cn("rounded-full px-2 py-0.5", l.tone)}>{l.label}</span>
                                                    {l.note}
                                                </span>
                                            ))}
                                        </div>
                                    </div>
                                    {data.totalPages > 1 && (
                                        <div className="flex flex-wrap items-center justify-between gap-3 pt-3 mt-3 border-t text-xs text-muted-foreground">
                                            <span>
                                                Showing {(data.page - 1) * data.pageSize + 1}–{Math.min(data.page * data.pageSize, data.matched)} of {data.matched}
                                            </span>
                                            <div className="flex items-center gap-2">
                                                <button disabled={data.page <= 1} onClick={() => setPage((pp) => Math.max(1, pp - 1))}
                                                    className="rounded-lg border px-3 py-1 disabled:opacity-40 enabled:hover:bg-muted">Prev</button>
                                                <span className="tabular-nums">Page {data.page} / {data.totalPages}</span>
                                                <button disabled={data.page >= data.totalPages} onClick={() => setPage((pp) => pp + 1)}
                                                    className="rounded-lg border px-3 py-1 disabled:opacity-40 enabled:hover:bg-muted">Next</button>
                                            </div>
                                        </div>
                                    )}
                                </Panel>
                            </div>
                        )}
                    </div>
                )}
            </div>
            <style>{`.ctrl{min-width:150px;border-radius:.5rem;border:1px solid var(--border);background:var(--background);padding:.4rem .6rem;font-size:.8rem}`}</style>
        </Page>
    );
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <label className="flex flex-col gap-1">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
            {children}
        </label>
    );
}

export default DashboardWorkflowPage;
