import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import type { IWorkerAppAggregate } from "../types/worker-app";

// Matches the convention every other component test in this feature uses
// (worker-replica-row.test.tsx, worker-summary-cards.test.tsx, live-pill.test.tsx):
// t is an identity function so `t("workers.identity_pending")` renders the raw
// key — exactly what Task 26 (locale entries not added yet) requires we prove.
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }));

const { default: WorkerStatusCell } = await import("./worker-status-cell");

// Mirrors the `t` stub registered above (identity: returns the raw key).
// Used so the pending-state assertion is tied to "whatever t() actually
// returns in this test environment", per review, rather than a hand-typed
// guess or a literal glyph the component no longer renders.
const t = (key: string) => key;

afterEach(() => cleanup());

const row = (p: Partial<IWorkerAppAggregate>): IWorkerAppAggregate => ({
    worker_app: "jira-worker",
    delivery_mode: "push",
    nodes_total: 22,
    nodes_healthy: 22,
    replicas_total: 1,
    replicas_healthy: 1,
    replica_identity: "known",
    oldest_heartbeat_age_seconds: 3,
    ...p,
});

const cell = () => screen.getByTestId("worker-status-cell");

describe("WorkerStatusCell", () => {
    it("renders a quiet dot and muted count when healthy", () => {
        render(<WorkerStatusCell row={row({ replicas_total: 2, replicas_healthy: 2 })} />);
        expect(cell().getAttribute("data-status")).toBe("ok");
        expect(cell().textContent).toContain("2 / 2");
        expect(cell().className).not.toContain("bg-status-warn");
        // The dot that carries the ok token lives inside this cell — sweep the
        // whole subtree, not just the outer span's own class list, or a raw
        // green class smuggled onto the dot would slip past unnoticed.
        expect(cell().innerHTML).not.toMatch(/(green|amber|red|emerald)-\d{3}/);
    });

    it("renders a warn pill when partially healthy", () => {
        render(<WorkerStatusCell row={row({ replicas_total: 2, replicas_healthy: 1 })} />);
        expect(cell().getAttribute("data-status")).toBe("warn");
        expect(cell().className).toContain("bg-status-warn/10");
    });

    it("renders a destructive pill when nothing is healthy", () => {
        render(<WorkerStatusCell row={row({ replicas_total: 2, replicas_healthy: 0 })} />);
        expect(cell().getAttribute("data-status")).toBe("dead");
        expect(cell().className).toContain("bg-destructive/10");
    });

    it("renders the pending translation and no count when identity is pending", () => {
        render(
            <WorkerStatusCell
                row={row({ replica_identity: "pending", replicas_total: 0, replicas_healthy: 0 })}
            />
        );
        expect(cell().getAttribute("data-status")).toBe("pending");
        expect(cell().textContent).toBe(t("workers.identity_pending"));
        // Discriminates: a pending row that fell through to the healthy/total
        // count (even "0 / 0") would fail this — counts are meaningless while
        // identity is pending, so nothing digit-shaped may appear here.
        expect(cell().textContent).not.toMatch(/\d+\s*\/\s*\d+/);
    });

    it("never uses a raw tailwind palette class", () => {
        render(<WorkerStatusCell row={row({ replicas_total: 2, replicas_healthy: 1 })} />);
        expect(cell().className).not.toMatch(/(green|amber|red|emerald)-\d{3}/);
    });
});
