// SA-2590 — Dashboard → Overview. Rolls up ALL workflows + workers from the
// HANA-backed analytics API (SA-2597). Real DB data, not mock.
import Page from "@/components/containers/page";
import { fetchOverview, type OverviewData } from "@/features/dashboard/api";
import { AreaLineChart, Donut, EmptyState, Kpi, Legend, Panel, RANGES, RankedBars } from "@/features/dashboard/dashboard-kit";
import { cn } from "@ldc/ui";
import { useEffect, useState } from "react";

const DashboardOverviewPage = () => {
    const [range, setRange] = useState("30d");
    const [data, setData] = useState<OverviewData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let alive = true;
        setLoading(true);
        setError(null);
        fetchOverview(range)
            .then((d) => alive && setData(d))
            .catch((e) => alive && setError(String(e.message || e)))
            .finally(() => alive && setLoading(false));
        return () => { alive = false; };
    }, [range]);

    return (
        <Page>
            <Page.Header
                title="Overview"
                description="Everything at a glance — workflows and workers across the tenant"
                actions={
                    <div className="pr-4">
                        <select value={range} onChange={(e) => setRange(e.target.value)} className="rounded-lg border bg-background px-3 py-1.5 text-sm">
                            {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                        </select>
                    </div>
                }
            />
            <div className="flex-1 overflow-y-auto px-4 pb-8">
                {loading && <div className="text-sm text-muted-foreground py-10 text-center animate-pulse">Loading analytics…</div>}
                {error && !loading && (
                    <Panel title="Couldn’t load analytics" subtitle="">
                        <EmptyState title="Analytics API unreachable" hint={error}
                            cta={<button onClick={() => setRange((r) => r)} className="mt-1 rounded-lg border px-3 py-1.5 text-sm hover:bg-muted">Retry</button>} />
                    </Panel>
                )}
                {data && !loading && !error && (
                    <>
                        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
                            <Kpi label="Total workflows" value={String(data.kpis.workflows)} />
                            <Kpi label={`Runs (${range})`} value={data.kpis.runs.toLocaleString()} />
                            <Kpi label="Success rate" value={`${data.kpis.successRate}%`} />
                            <Kpi label="Active workers" value={String(data.kpis.activeWorkers)} />
                        </div>
                        <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
                            <Panel title="Runs over time" subtitle="Successful vs failed executions · all workflows" className="lg:col-span-8">
                                <AreaLineChart success={data.runsOverTime.map((p) => p.success)} failed={data.runsOverTime.map((p) => p.failed)} labels={data.runsOverTime.map((p) => p.date)} />
                                <Legend items={[{ color: "#10b981", label: "Success" }, { color: "#f43f5e", label: "Failed" }]} />
                            </Panel>
                            <Panel title="Run outcomes" subtitle={`Last ${range}`} className="lg:col-span-4">
                                <Donut success={data.outcomes.success} failed={data.outcomes.failed} />
                                <Legend items={[{ color: "#10b981", label: `Success ${data.outcomes.success.toLocaleString()}` }, { color: "#f43f5e", label: `Failed ${data.outcomes.failed.toLocaleString()}` }]} />
                            </Panel>
                            <Panel title="Top workflows by runs" subtitle="Most-used workflows · full detail on the Workflow tab" className="lg:col-span-6">
                                {data.topWorkflows.length ? <RankedBars items={data.topWorkflows} /> : <EmptyState title="No runs in range" />}
                            </Panel>
                            <Panel title="Workers — activity & health" subtitle="Worker analytics coming soon (SA-2596)" className="lg:col-span-6">
                                {data.workers.length ? (
                                    <table className="w-full text-sm">
                                        <thead><tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                                            <th className="py-1.5 font-medium">Worker</th><th className="py-1.5 font-medium">Type</th>
                                            <th className="py-1.5 font-medium text-right">Tasks</th><th className="py-1.5 font-medium text-right">Status</th></tr></thead>
                                        <tbody>{data.workers.map((w) => (
                                            <tr key={w.name} className="border-t"><td className="py-2">{w.name}</td>
                                                <td className="py-2 text-muted-foreground tabular-nums">{w.type}</td>
                                                <td className="py-2 text-right tabular-nums">{w.tasks.toLocaleString()}</td>
                                                <td className="py-2 text-right"><span className={cn("rounded-full px-2 py-0.5 text-xs", w.status === "online" ? "bg-emerald-500/15 text-emerald-600" : "bg-rose-500/15 text-rose-600")}>{w.status}</span></td></tr>))}
                                        </tbody></table>
                                ) : <EmptyState title="No worker metrics yet" hint="Worker-level analytics land in a later iteration." />}
                            </Panel>
                        </div>
                    </>
                )}
            </div>
        </Page>
    );
};

export default DashboardOverviewPage;
