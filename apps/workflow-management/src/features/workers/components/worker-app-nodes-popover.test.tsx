import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Matches the convention every other component test in this feature uses
// (worker-status-cell.test.tsx, worker-replica-row.test.tsx, live-pill.test.tsx):
// t is an identity function so `t("workers.col.nodes")` renders the raw key —
// exactly what Task 26 (locale entries not added yet) requires we prove.
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }));

const { default: WorkerAppNodesPopover } = await import("./worker-app-nodes-popover");

afterEach(() => cleanup());

describe("WorkerAppNodesPopover", () => {
    it("shows the node count on the chip, not one entry per node", () => {
        render(<WorkerAppNodesPopover count={22} nodes={[]} />);
        // 22 jira node types collapsed to a single trigger — the whole bug this
        // component exists to fix. Only one button may exist for the chip.
        expect(screen.getAllByRole("button")).toHaveLength(1);
        expect(screen.getByRole("button").textContent).toContain("22");
    });

    it("does not render the node list before the popover is opened", () => {
        render(<WorkerAppNodesPopover count={2} nodes={["jira_create_issue", "jira_get_issue"]} />);
        // Radix mounts PopoverContent into a portal only once open. If a future
        // change rendered the list unconditionally (defeating the point of the
        // popover — hiding the catalog until asked for), this would catch it.
        expect(screen.queryByText("jira_create_issue")).toBeNull();
        expect(screen.queryByText("jira_get_issue")).toBeNull();
    });

    it("lists the node types once opened", async () => {
        render(<WorkerAppNodesPopover count={2} nodes={["jira_create_issue", "jira_get_issue"]} />);
        await userEvent.click(screen.getByRole("button"));
        // findBy* because Radix's Popover.Content mounts into a portal
        // asynchronously after the trigger fires — a plain getByText here would
        // be racy, not a real signal the list failed to render.
        expect(await screen.findByText("jira_create_issue")).toBeTruthy();
        expect(screen.getByText("jira_get_issue")).toBeTruthy();
    });
});
