import type { IWorkerAppAggregate, IWorkerAppHealthPayload } from "../types/worker-app";

export type AppReplicaStatus = "pending" | "ok" | "warn" | "dead";

/**
 * "pending" wins over every count: when the registry was never told which
 * process a registration came from, the counts are zeros standing in for
 * unknown, and reading them as "dead" would invent an outage.
 */
export const appReplicaStatus = (
    row: Pick<IWorkerAppAggregate, "replicas_total" | "replicas_healthy" | "replica_identity">
): AppReplicaStatus => {
    if (row.replica_identity === "pending") return "pending";
    if (row.replicas_healthy === 0) return "dead";
    if (row.replicas_healthy === row.replicas_total && row.replicas_total > 0) return "ok";
    return "warn";
};

export interface IWorkerAppSummary {
    workers: number;
    healthy: number;
    total: number;
    pending: number;
    degraded: number;
    workersAtZero: number;
}

export const summaryCardStats = (rows: IWorkerAppAggregate[]): IWorkerAppSummary => {
    const known = rows.filter((r) => r.replica_identity === "known");
    return {
        workers: rows.length,
        healthy: known.reduce((s, r) => s + r.replicas_healthy, 0),
        total: known.reduce((s, r) => s + r.replicas_total, 0),
        pending: rows.length - known.length,
        degraded: known.filter((r) => r.replicas_healthy > 0 && r.replicas_healthy < r.replicas_total).length,
        // An app whose nodes are all unhealthy is the one leaving the palette.
        // A pending app is excluded: unknown is not zero.
        workersAtZero: known.filter((r) => r.nodes_healthy === 0).length,
    };
};

export const humaniseAge = (seconds: number | null | undefined): string => {
    // Guard null/undefined/NaN too: a realtime delta may omit the age, and
    // humaniseAge(undefined) must render "—", never "NaNm NaNs ago".
    if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "—";
    const s = Math.floor(seconds);
    if (s < 60) return `${s}s ago`;
    return `${Math.floor(s / 60)}m ${s % 60}s ago`;
};

export const patchAppAggregate = (
    prev: IWorkerAppAggregate[],
    payload: IWorkerAppHealthPayload
): IWorkerAppAggregate[] => {
    const { at: _at, ...next } = payload;
    const idx = prev.findIndex((r) => r.worker_app === payload.worker_app);
    if (idx === -1) return [...prev, next];
    const copy = prev.slice();
    copy[idx] = next;
    return copy;
};
