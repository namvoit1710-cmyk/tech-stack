import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import type { IWorkerAppAggregate } from "@/features/workers/types/worker-app";

// Matches the convention every other component test in this feature uses
// (worker-status-cell.test.tsx, worker-app-row.test.tsx, live-pill.test.tsx):
// t is an identity function so `t("workers.cards.*")` renders the raw key —
// exactly what Task 26 (locale entries not added yet) requires we prove.
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }));

const { default: WorkerSummaryCards } = await import("./worker-summary-cards");

afterEach(() => cleanup());

const row = (p: Partial<IWorkerAppAggregate>): IWorkerAppAggregate => ({
    worker_app: "jira-worker",
    delivery_mode: "push",
    nodes_total: 10,
    nodes_healthy: 10,
    replicas_total: 1,
    replicas_healthy: 1,
    replica_identity: "known",
    oldest_heartbeat_age_seconds: 3,
    ...p,
});

describe("WorkerSummaryCards", () => {
    it("derives the four cards from summaryCardStats over app rows, excluding the pending app from the ratio, degraded, and workers-at-0", () => {
        const rows: IWorkerAppAggregate[] = [
            row({ worker_app: "a", replicas_total: 4, replicas_healthy: 4, nodes_healthy: 10 }),
            row({ worker_app: "b", replicas_total: 3, replicas_healthy: 2, nodes_healthy: 5 }),
            row({ worker_app: "c", replicas_total: 2, replicas_healthy: 0, nodes_healthy: 0 }),
            // Pending, but carrying NON-ZERO counts that would look degraded (1 of
            // 3 healthy) and at-zero (nodes_healthy 0) if the identity filter were
            // ever dropped. An all-zero pending row could not prove exclusion —
            // it would pass a broken filter just as easily as a correct one.
            row({
                worker_app: "stale-pending",
                replica_identity: "pending",
                replicas_total: 3,
                replicas_healthy: 1,
                nodes_healthy: 0,
            }),
        ];
        render(<WorkerSummaryCards rows={rows} />);

        // workers: row count INCLUDES the pending app (4 rows total).
        expect(screen.getByTestId("card-workers").textContent).toBe("4");
        // healthy/total: sums only the 3 known apps (4+2+0=6 of 4+3+2=9). If the
        // pending app's 1/3 leaked into the sum this would read "7 / 12" instead.
        expect(screen.getByTestId("card-healthy").textContent).toContain("6 / 9");
        expect(screen.getByTestId("card-healthy").textContent).not.toContain("7 / 12");
        // degraded: only "b" (2 of 3 healthy) is known+partial. The pending app is
        // ALSO 1-of-3 (partial) by raw counts but must not be counted -> stays 1.
        expect(screen.getByTestId("card-degraded").textContent).toBe("1");
        // workers at 0: only "c" (nodes_healthy 0) is known+zero. The pending app
        // is ALSO nodes_healthy 0 but must not be counted -> stays 1, not 2.
        expect(screen.getByTestId("card-draining").textContent).toBe("1");
    });

    it("shows the pending count as a suffix on the healthy card when pending > 0", () => {
        const rows: IWorkerAppAggregate[] = [
            row({ worker_app: "a" }),
            row({
                worker_app: "stale-pending",
                replica_identity: "pending",
                replicas_total: 0,
                replicas_healthy: 0,
            }),
        ];
        render(<WorkerSummaryCards rows={rows} />);
        // t is the identity stub registered above, so the raw i18n key is exactly
        // what must appear — proves the copy is not hardcoded.
        expect(screen.getByTestId("card-healthy").textContent).toContain("workers.cards.pending_suffix");
    });

    it("omits the pending suffix entirely when there is no pending app", () => {
        const rows: IWorkerAppAggregate[] = [row({ worker_app: "a" }), row({ worker_app: "b" })];
        render(<WorkerSummaryCards rows={rows} />);
        expect(screen.getByTestId("card-healthy").textContent).not.toContain("pending_suffix");
    });

    it("never uses a raw tailwind palette class", () => {
        const rows: IWorkerAppAggregate[] = [
            // healthy > 0 and < total on a known app trips the degraded warn path —
            // the one place this component conditionally colours a card.
            row({ worker_app: "a", replicas_total: 2, replicas_healthy: 1, nodes_healthy: 5 }),
        ];
        const { container } = render(<WorkerSummaryCards rows={rows} />);
        expect(container.innerHTML).not.toMatch(/(green|amber|red|emerald)-\d{3}/);
    });
});
