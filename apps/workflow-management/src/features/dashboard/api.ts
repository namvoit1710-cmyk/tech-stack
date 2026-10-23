// SA-2590/SA-2597 — Dashboard analytics API client. Talks to the control-plane
// analytics endpoints (same base + BFF session cookie as the rest of the app).
import { env } from "@/env";

const BASE = `${env.PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE}/api/v1/analytics`;
const CREDENTIALS: RequestCredentials = env.PUBLIC_AUTH_MODE === "bff" ? "include" : "same-origin";

export interface OverviewData {
    range: string;
    kpis: { workflows: number; runs: number; successRate: number; activeWorkers: number };
    runsOverTime: { date: string; success: number; failed: number }[];
    outcomes: { success: number; failed: number };
    topWorkflows: { label: string; value: number }[];
    workers: { name: string; type: string; tasks: number; status: string }[];
}

export interface WorkflowRow {
    id: string; name: string; owner: string; status: string;
    runs: number; successRate: number; avgMs: number; lastRun: string;
    direct?: number; sub?: number;  // SA-2626: Runs-cell hover breakdown (runs = direct + sub)
}

export interface WorkflowsData {
    range: string;
    filters: { owner: string; q: string; status: string };
    matched: number; total: number; runsInRange: number; avgRuns: number;
    savedSeconds?: number;  // SA-2778: Σ (successful in-range runs × manual work), filter-scoped
    ranked: { label: string; value: number }[];
    trend: number[];
    trendLabels?: string[];  // SA-2625: bucket start-date per trend bar (x-axis labels)
    rows: WorkflowRow[];
    page: number; pageSize: number; totalPages: number;
}

async function getJson<T>(path: string): Promise<T> {
    const res = await fetch(`${BASE}${path}`, { credentials: CREDENTIALS });
    if (!res.ok) throw new Error(`analytics ${path} → ${res.status}`);
    // control-plane wraps successful responses in { status, data }
    const json = await res.json();
    return (json?.data ?? json) as T;
}

export const fetchOverview = (range: string) =>
    getJson<OverviewData>(`/overview?range=${encodeURIComponent(range)}`);

export const fetchWorkflows = (p: {
    range: string; owner?: string; q?: string; status?: string; page?: number; pageSize?: number;
}) => {
    const qs = new URLSearchParams({ range: p.range });
    if (p.owner) qs.set("owner", p.owner);
    if (p.q) qs.set("q", p.q);
    if (p.status && p.status !== "all") qs.set("status", p.status);
    if (p.page) qs.set("page", String(p.page));
    if (p.pageSize) qs.set("pageSize", String(p.pageSize));
    return getJson<WorkflowsData>(`/workflows?${qs.toString()}`);
};
