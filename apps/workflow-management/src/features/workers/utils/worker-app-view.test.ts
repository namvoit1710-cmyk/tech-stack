import { describe, expect, it } from "@rstest/core";
import { appReplicaStatus, humaniseAge, patchAppAggregate, summaryCardStats } from "./worker-app-view";
import type { IWorkerAppAggregate } from "../types/worker-app";

const row = (p: Partial<IWorkerAppAggregate>): IWorkerAppAggregate => ({
    worker_app: "jira-worker", delivery_mode: "push",
    nodes_total: 22, nodes_healthy: 22,
    replicas_total: 1, replicas_healthy: 1,
    replica_identity: "known", oldest_heartbeat_age_seconds: 3, ...p,
});

describe("appReplicaStatus", () => {
    it("is pending before identity is known, whatever the counts say", () => {
        expect(appReplicaStatus(row({ replica_identity: "pending", replicas_total: 0, replicas_healthy: 0 })))
            .toBe("pending");
    });
    it("is ok when every replica is healthy", () => {
        expect(appReplicaStatus(row({ replicas_total: 3, replicas_healthy: 3 }))).toBe("ok");
    });
    it("is warn when some replicas are healthy", () => {
        expect(appReplicaStatus(row({ replicas_total: 3, replicas_healthy: 1 }))).toBe("warn");
    });
    it("is dead when none are healthy", () => {
        expect(appReplicaStatus(row({ replicas_total: 3, replicas_healthy: 0 }))).toBe("dead");
    });
    // Migrated from the deleted worker-replica-view.test.ts's "red when zero
    // total (leaving palette)" case: a known-identity app whose total has also
    // dropped to zero (the worker process has fully left the fleet) must still
    // read as dead, not fall through some other branch just because total=0 too.
    it("is dead when total and healthy are both zero for a known identity (worker has left the fleet)", () => {
        expect(appReplicaStatus(row({ replicas_total: 0, replicas_healthy: 0 }))).toBe("dead");
    });
});

describe("summaryCardStats", () => {
    it("counts workers, and excludes pending apps from the healthy ratio", () => {
        const stats = summaryCardStats([
            row({ worker_app: "a", replicas_total: 2, replicas_healthy: 2 }),
            row({ worker_app: "b", replicas_total: 2, replicas_healthy: 1 }),
            row({ worker_app: "c", replica_identity: "pending", replicas_total: 0, replicas_healthy: 0 }),
        ]);
        expect(stats.workers).toBe(3);
        expect(stats.total).toBe(4);
        expect(stats.healthy).toBe(3);
        expect(stats.pending).toBe(1);
        expect(stats.degraded).toBe(1);
    });
    it("does not count a pending app as degraded or at-zero", () => {
        const stats = summaryCardStats([row({ replica_identity: "pending", replicas_total: 0, replicas_healthy: 0 })]);
        expect(stats.degraded).toBe(0);
        expect(stats.workersAtZero).toBe(0);
    });
    it("counts an app whose nodes are all unhealthy as at-zero", () => {
        expect(summaryCardStats([row({ nodes_healthy: 0 })]).workersAtZero).toBe(1);
    });
    // The fixtures above never give a pending row non-zero/unhealthy counts, so a
    // ratio/degraded/at-zero calculation that forgot to filter on replica_identity
    // would pass them too (0 counts sum to 0 and can never look "degraded" either
    // way). These fixtures force the exclusion to do real work: a pending row that
    // *would* look healthy/degraded/at-zero by its raw numbers must still be
    // ignored, because identity gates before counts — same rule as appReplicaStatus.
    it("excludes a pending app's counts from the healthy ratio even if they look non-zero", () => {
        const stats = summaryCardStats([
            row({ worker_app: "a", replicas_total: 2, replicas_healthy: 2 }),
            row({ worker_app: "stale-pending", replica_identity: "pending", replicas_total: 5, replicas_healthy: 5 }),
        ]);
        expect(stats.total).toBe(2);
        expect(stats.healthy).toBe(2);
        expect(stats.workers).toBe(2);
        expect(stats.pending).toBe(1);
    });
    it("excludes a pending app from degraded and workersAtZero even if its counts look unhealthy", () => {
        const stats = summaryCardStats([
            row({ worker_app: "stale-pending", replica_identity: "pending", replicas_total: 3, replicas_healthy: 1, nodes_healthy: 0 }),
        ]);
        expect(stats.degraded).toBe(0);
        expect(stats.workersAtZero).toBe(0);
    });
    // Migrated from the deleted worker-replica-view.test.ts's "is all-zero for
    // an empty fleet" case.
    it("is all-zero for an empty fleet", () => {
        expect(summaryCardStats([])).toEqual({ workers: 0, healthy: 0, total: 0, pending: 0, degraded: 0, workersAtZero: 0 });
    });
});

describe("patchAppAggregate", () => {
    it("upserts by worker_app and leaves others untouched", () => {
        const prev = [row({ worker_app: "a" }), row({ worker_app: "b" })];
        const next = patchAppAggregate(prev, { ...row({ worker_app: "b", nodes_healthy: 1 }), at: "2026-07-28T02:00:00Z" });
        expect(next).toHaveLength(2);
        expect(next[1]?.nodes_healthy).toBe(1);
        expect(next[0]).toBe(prev[0]);
    });
    it("appends an app it has not seen", () => {
        expect(patchAppAggregate([], { ...row({ worker_app: "new" }), at: "x" })).toHaveLength(1);
    });
    // Migrated from the deleted worker-replica-view.test.ts's "a total=0 delta
    // keeps the row (so the red transition is visible)" case: patching an app to
    // 0/0 must not drop it from the array -- that's what makes the dead
    // transition visible in the UI instead of the row silently vanishing.
    it("a zero delta keeps the row so the dead transition is visible", () => {
        const prev = [row({ worker_app: "echo", replicas_total: 3, replicas_healthy: 2 })];
        const next = patchAppAggregate(prev, { ...row({ worker_app: "echo", replicas_total: 0, replicas_healthy: 0 }), at: "x" });
        const echo = next.find((r) => r.worker_app === "echo");
        if (!echo) throw new Error("expected the echo row to survive the patch");
        expect(appReplicaStatus(echo)).toBe("dead");
    });
    // Migrated from the deleted worker-replica-view.test.ts's "does not mutate
    // the previous array" case.
    it("does not mutate the previous array", () => {
        const prev = [row({ worker_app: "a" }), row({ worker_app: "b" })];
        const snapshot = JSON.stringify(prev);
        patchAppAggregate(prev, { ...row({ worker_app: "b", nodes_healthy: 1 }), at: "x" });
        expect(JSON.stringify(prev)).toBe(snapshot);
    });
});

describe("humaniseAge", () => {
    it("renders an em dash for an unmeasurable age", () => {
        expect(humaniseAge(null)).toBe("—");
        expect(humaniseAge(undefined)).toBe("—");
    });
    it("renders seconds and minutes", () => {
        expect(humaniseAge(3)).toBe("3s ago");
        expect(humaniseAge(134)).toBe("2m 14s ago");
    });
});
