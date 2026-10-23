import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { IWorkerAppAggregate, IWorkerAppInstance } from "@/features/workers/types/worker-app";

// Matches the convention every other component test in this feature uses
// (worker-replica-row.test.tsx, worker-status-cell.test.tsx, worker-app-nodes-popover.test.tsx):
// t is an identity function so `t("workers.col.nodes")` renders the raw key —
// exactly what Task 26 (locale entries not added yet) requires we prove.
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }));

const useQueryWorkerAppInstances = rs.fn();
rs.mock("@/features/workers/hooks/apis/use-worker-app-instances-query", () => ({
    useQueryWorkerAppInstances: (...a: unknown[]) => useQueryWorkerAppInstances(...a),
}));

const { default: WorkerAppRow } = await import("./worker-app-row");

afterEach(() => cleanup());

const row = (over: Partial<IWorkerAppAggregate> = {}): IWorkerAppAggregate => ({
    worker_app: "jira-worker",
    delivery_mode: "push",
    nodes_total: 22,
    nodes_healthy: 22,
    replicas_total: 3,
    replicas_healthy: 3,
    replica_identity: "known",
    oldest_heartbeat_age_seconds: 134,
    node_types: ["jira_create_issue", "jira_get_issue"],
    ...over,
});

const instance = (over: Partial<IWorkerAppInstance> = {}): IWorkerAppInstance => ({
    instance_id: "a1c9f012",
    status: "healthy",
    last_heartbeat: "2026-07-28T00:00:00Z",
    age_seconds: 4,
    host: "mbp",
    version: "v1.3.0",
    sdk_version: "sdk-1.0.0",
    started_at: "2026-07-28T00:00:00Z",
    delivery_mode: "push",
    nodes: ["jira_create_issue"],
    ...over,
});

const nodesChipButton = () => screen.getByRole("button", { name: "workers.col.nodes" });
// The row's accessible expand control is the chevron CollapsibleTrigger — its
// aria-label is built as `${t(key)} ${row.worker_app}`, so with the identity
// `t` stub it reads "workers.expand_processes jira-worker". Matching on the
// app name keeps this selector unique from the nodes chip button.
const expandButton = () => screen.getByRole("button", { name: /jira-worker/i });

describe("WorkerAppRow", () => {
    it("renders the app name, delivery chip, nodes chip and status", () => {
        useQueryWorkerAppInstances.mockReturnValue({ data: undefined, isLoading: false });
        render(<WorkerAppRow row={row()} />);

        expect(screen.getByText("jira-worker")).toBeTruthy();
        expect(screen.getByText("push")).toBeTruthy();
        // The chip's count comes from the aggregate (nodes_total) and must be
        // correct even though the row has never been expanded.
        expect(nodesChipButton().textContent).toContain("22");
        // Reuses WorkerStatusCell (Task 19) rather than re-deriving the status rule.
        expect(screen.getByTestId("worker-status-cell")).toBeTruthy();
        expect(screen.getByText("3 / 3")).toBeTruthy();
        expect(screen.getByText("2m 14s ago")).toBeTruthy();
        // The expand control has an accessible name naming the app it expands.
        expect(expandButton()).toBeTruthy();
    });

    it("does not nest an interactive element inside another button", () => {
        // A <button> cannot legally contain another interactive element
        // (WorkerAppNodesPopover renders its own <button> for the chip).
        // Structural regression guard for that content-model rule, checked
        // against actual rendered markup, not just the JSX source.
        useQueryWorkerAppInstances.mockReturnValue({ data: undefined, isLoading: false });
        const { container } = render(<WorkerAppRow row={row()} />);
        expect(container.querySelectorAll("button button")).toHaveLength(0);
    });

    it("does not fetch instances until the row is expanded", async () => {
        useQueryWorkerAppInstances.mockReturnValue({ data: [], isLoading: false });
        const user = userEvent.setup();
        render(<WorkerAppRow row={row()} />);

        // Discriminates from "no instance rows visible yet" (also true while a
        // request is in flight): asserts the hook itself was called with
        // enabled=false, i.e. no request was ever fired.
        expect(useQueryWorkerAppInstances).toHaveBeenCalledWith("jira-worker", false);
        expect(useQueryWorkerAppInstances).not.toHaveBeenCalledWith("jira-worker", true);

        await user.click(expandButton());
        expect(useQueryWorkerAppInstances).toHaveBeenLastCalledWith("jira-worker", true);
    });

    it("does not expand the row when the nodes chip is clicked", async () => {
        // Clear call history: this assertion checks the mock's entire call
        // history (unlike toHaveBeenLastCalledWith), so a `true` call left
        // over from an earlier test in this file would otherwise produce a
        // false failure unrelated to this test's own click.
        useQueryWorkerAppInstances.mockClear();
        useQueryWorkerAppInstances.mockReturnValue({ data: undefined, isLoading: false });
        const user = userEvent.setup();
        render(<WorkerAppRow row={row()} />);

        await user.click(nodesChipButton());

        // The nodes chip and the row's expand control are now siblings, not
        // nested — clicking the chip must not also toggle the instance list.
        // If it did, the lazy-fetch hook would have been called with
        // enabled=true as a side effect of that click.
        expect(useQueryWorkerAppInstances).not.toHaveBeenCalledWith("jira-worker", true);
    });

    it("lists one instance row per process after expanding", async () => {
        const instances = [
            instance({ instance_id: "inst-1", version: "v1.3.0" }),
            instance({ instance_id: "inst-2", version: "v1.4.0" }),
        ];
        useQueryWorkerAppInstances.mockReturnValue({ data: instances, isLoading: false });
        const user = userEvent.setup();
        render(<WorkerAppRow row={row()} />);

        await user.click(expandButton());

        // Two processes -> two rows, not one row with both processes folded in.
        expect(screen.getAllByTestId("worker-app-instance-row")).toHaveLength(2);
        expect(screen.getByText("inst-1")).toBeTruthy();
        expect(screen.getByText("inst-2")).toBeTruthy();
        expect(screen.getByText("v1.3.0")).toBeTruthy();
        expect(screen.getByText("v1.4.0")).toBeTruthy();
    });

    it("lists the node types in the popover on a COLLAPSED row", async () => {
        // The defect this replaces: `nodes` was read off the lazily-fetched
        // instance list, which only loads once the row is expanded — so the
        // chip opened an empty box on every collapsed row, i.e. every row.
        // Witnessed live before the fix (R14 real-worker evidence note).
        //
        // mockClear first: the enabled=false assertion below reads the mock's
        // WHOLE call history, so an enabled=true call left by an earlier test
        // in this file would fail it for reasons that have nothing to do with
        // this render (same reason the chip-click test clears it).
        useQueryWorkerAppInstances.mockClear();
        useQueryWorkerAppInstances.mockReturnValue({ data: undefined, isLoading: false });
        const user = userEvent.setup();
        render(<WorkerAppRow row={row({ nodes_total: 2 })} />);

        await user.click(nodesChipButton());

        expect(await screen.findByText("jira_create_issue")).toBeTruthy();
        expect(screen.getByText("jira_get_issue")).toBeTruthy();
        // Discriminates "the list happens to be there" from "the list is there
        // WITHOUT the instance fetch": the row is still collapsed, so the hook
        // was never enabled, and the names can only have come from the row.
        expect(useQueryWorkerAppInstances).not.toHaveBeenCalledWith("jira-worker", true);
    });

    it("ignores the instance list when filling the popover", async () => {
        // A loaded instance advertising DIFFERENT names must not win: the
        // catalogue belongs to the app, and instances[0] is one process's view.
        const instances = [instance({ nodes: ["stale_from_instance"] })];
        useQueryWorkerAppInstances.mockReturnValue({ data: instances, isLoading: false });
        const user = userEvent.setup();
        render(<WorkerAppRow row={row({ node_types: ["jira_create_issue"] })} />);

        await user.click(expandButton());
        await user.click(nodesChipButton());

        expect(await screen.findByText("jira_create_issue")).toBeTruthy();
        expect(screen.queryByText("stale_from_instance")).toBeNull();
    });

    it("shows an empty-state in the popover when the row carries no node types", async () => {
        // A control-plane that predates node_types sends the field absent.
        useQueryWorkerAppInstances.mockReturnValue({ data: undefined, isLoading: false });
        const user = userEvent.setup();
        render(<WorkerAppRow row={row({ node_types: undefined })} />);

        await user.click(nodesChipButton());
        expect(await screen.findByText("workers.no_node_types")).toBeTruthy();
    });

    it("shows one skeleton row per expected process while instances load", async () => {
        useQueryWorkerAppInstances.mockReturnValue({ data: undefined, isLoading: true });
        const user = userEvent.setup();
        render(<WorkerAppRow row={row({ replicas_total: 3, replicas_healthy: 3 })} />);

        await user.click(expandButton());

        // The summary row already knows the process count, so the placeholder
        // is the right height and the list does not jump when data lands.
        expect(screen.getAllByTestId("worker-app-instance-skeleton")).toHaveLength(3);
        // Column labels are up before the data, not after.
        expect(screen.getByText("workers.inst.process")).toBeTruthy();
    });

    it("shows a single skeleton row when the replica count is pending", async () => {
        // replicas_total is 0-standing-in-for-unknown here; rendering zero
        // skeletons would read as "no processes", which is the same lie the
        // count itself is not allowed to tell.
        useQueryWorkerAppInstances.mockReturnValue({ data: undefined, isLoading: true });
        const user = userEvent.setup();
        render(<WorkerAppRow row={row({ replica_identity: "pending", replicas_total: 0, replicas_healthy: 0 })} />);

        await user.click(expandButton());
        expect(screen.getAllByTestId("worker-app-instance-skeleton")).toHaveLength(1);
    });

    it("shows an inline error when the instance fetch fails", async () => {
        useQueryWorkerAppInstances.mockReturnValue({ data: undefined, isLoading: false, isError: true });
        const user = userEvent.setup();
        render(<WorkerAppRow row={row()} />);
        await user.click(expandButton());
        expect(screen.getByText("workers.instance_error")).toBeTruthy();
    });
});
