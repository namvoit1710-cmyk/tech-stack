import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import type { IWorkerAppAggregate } from "@/features/workers/types/worker-app";

rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }));

const useQueryWorkerApps = rs.fn();
rs.mock("@/features/workers/hooks/apis/use-worker-apps-query", () => ({
    useQueryWorkerApps: () => useQueryWorkerApps(),
}));
// WorkerAppRow (Task 22) calls this on every render; keep it disabled/empty here
// since no row is ever expanded in this page-level test.
rs.mock("@/features/workers/hooks/apis/use-worker-app-instances-query", () => ({
    useQueryWorkerAppInstances: () => ({ data: undefined, isLoading: false }),
}));
rs.mock("@/features/workers/hooks/use-worker-health-sync", () => ({
    useWorkerHealthSync: () => ({ connected: true }),
}));

// The real Page container depends on a SidebarProvider context (see
// src/pages/replay-debug.test.tsx for the established precedent); mock the shell.
rs.mock("@/components/containers/page", () => {
    const Page = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
    Page.Header = ({ title, actions }: { title?: ReactNode; actions?: ReactNode }) => (
        <div>{title}{actions}</div>
    );
    return { default: Page };
});

const { default: WorkerHealthPage } = await import("./worker-health");

afterEach(() => cleanup());

const row = (over: Partial<IWorkerAppAggregate> = {}): IWorkerAppAggregate => ({
    worker_app: "jira-worker",
    delivery_mode: "push",
    nodes_total: 22,
    nodes_healthy: 22,
    replicas_total: 3,
    replicas_healthy: 3,
    replica_identity: "known",
    oldest_heartbeat_age_seconds: 3,
    ...over,
});

// Three app aggregates -- each app fans out to many node types under the hood
// (jira-worker alone owns 22), which is exactly what must NOT turn into 22
// rows any more.
const rows: IWorkerAppAggregate[] = [
    row({ worker_app: "jira-worker" }),
    row({ worker_app: "echo-worker", delivery_mode: "pull", nodes_total: 4, nodes_healthy: 4, replicas_total: 2, replicas_healthy: 2 }),
    row({ worker_app: "teams-worker", nodes_total: 6, nodes_healthy: 5, replicas_total: 3, replicas_healthy: 1 }),
];

describe("WorkerHealthPage", () => {
    it("renders one WorkerAppRow per app aggregate, not one row per node type", () => {
        useQueryWorkerApps.mockReturnValue({ data: rows, isError: false });
        render(<WorkerHealthPage />);
        // Three app aggregates -> three status cells (one per WorkerAppRow). If the
        // page still rendered per node-type, jira-worker's 22 nodes alone would
        // blow this count past 3 -- the exact bug this task fixes.
        expect(screen.getAllByTestId("worker-status-cell")).toHaveLength(3);
        expect(screen.getByText("jira-worker")).toBeTruthy();
        expect(screen.getByText("echo-worker")).toBeTruthy();
        expect(screen.getByText("teams-worker")).toBeTruthy();
        expect(screen.getByTestId("card-workers").textContent).toBe("3");
    });

    it("renders the header column labels from the app-identity keys", () => {
        useQueryWorkerApps.mockReturnValue({ data: rows, isError: false });
        render(<WorkerHealthPage />);
        expect(screen.getByText("workers.col.worker")).toBeTruthy();
        expect(screen.getByText("workers.col.nodes")).toBeTruthy();
    });

    it("renders the empty state without fabricating rows", () => {
        useQueryWorkerApps.mockReturnValue({ data: [], isError: false });
        render(<WorkerHealthPage />);
        expect(screen.getByText("workers.empty")).toBeTruthy();
    });

    it("renders an inline error when the snapshot fails", () => {
        useQueryWorkerApps.mockReturnValue({ data: undefined, isError: true });
        render(<WorkerHealthPage />);
        expect(screen.getByText("workers.load_error")).toBeTruthy();
    });

    it("shows skeleton rows and suppresses the empty state on first load", () => {
        useQueryWorkerApps.mockReturnValue({ data: undefined, isLoading: true, isError: false });
        render(<WorkerHealthPage />);
        // Skeleton rows, not a spinner over an empty box.
        expect(screen.getByTestId("workers-skeleton")).toBeTruthy();
        // And the cards must not state "0 workers" before anything was measured.
        expect(screen.getByTestId("card-workers").textContent).not.toContain("0");
        expect(screen.getByTestId("card-healthy").textContent).not.toContain("0");
        // The live region still announces the load for screen readers.
        expect(screen.getByTestId("workers-loading").getAttribute("aria-hidden")).toBe("false");
        expect(screen.queryByText("workers.empty")).toBeNull();
    });

    it("drops the skeleton once data has arrived", () => {
        useQueryWorkerApps.mockReturnValue({ data: rows, isLoading: false, isError: false });
        render(<WorkerHealthPage />);
        expect(screen.queryByTestId("workers-skeleton")).toBeNull();
        expect(screen.getByTestId("workers-loading").getAttribute("aria-hidden")).toBe("true");
    });

    it("keeps the rows and shows no skeleton when refetching with data present", () => {
        // A background refetch must not blank the table out from under the user.
        useQueryWorkerApps.mockReturnValue({ data: rows, isLoading: false, isFetching: true, isError: false });
        render(<WorkerHealthPage />);
        expect(screen.queryByTestId("workers-skeleton")).toBeNull();
        expect(screen.getByText("jira-worker")).toBeTruthy();
    });

    it("shows a subtle refresh indicator when refetching with data present", () => {
        useQueryWorkerApps.mockReturnValue({ data: rows, isLoading: false, isFetching: true, isError: false });
        render(<WorkerHealthPage />);
        expect(screen.getByTestId("workers-refreshing")).toBeTruthy();
    });

    it("does not show the refresh indicator during the initial load", () => {
        useQueryWorkerApps.mockReturnValue({ data: undefined, isLoading: true, isFetching: true, isError: false });
        render(<WorkerHealthPage />);
        expect(screen.queryByTestId("workers-refreshing")).toBeNull();
    });
});
