import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import type { ReactNode } from "react";

const mockNavigate = rs.fn();

rs.mock("react-router-dom", () => ({
    useParams: () => ({ workflowId: "wf-1", runId: "run-1" }),
    useNavigate: () => mockNavigate,
}));

// Rete canvas — mock to a div that exposes the readOnly prop we care about.
rs.mock("@ldc/workflow-editor", () => ({
    WorkflowEditor: (props: { readOnly?: boolean }) => (
        <div data-testid="wf-editor" data-readonly={String(!!props.readOnly)} />
    ),
}));

rs.mock("@/features/workflows/hooks/use-workflow-detail-page", () => ({
    useWorkflowDetail: () => ({ workflow: { name: "My Workflow" }, isLoading: false, switchVersion: () => {} }),
}));

rs.mock("@/features/workflows/stores/editor-stores", () => ({
    useEditorStore: (selector: (s: unknown) => unknown) =>
        selector({ workflowData: { nodes: [], connections: [] } }),
}));

// Scrubber is exercised in its own suite; here we only assert it's wired with the
// runId + the editor handle so it can paint the read-only canvas.
rs.mock("@/features/workflows/components/execution-history/replay-scrubber", () => ({
    default: (props: { runId: string; editorRef?: unknown }) => (
        <div data-testid="scrubber-mock" data-runid={props.runId} data-has-editor={String(!!props.editorRef)} />
    ),
}));

rs.mock("@ldc/ui/components/resizable", () => ({
    ResizablePanelGroup: ({ children }: { children?: ReactNode }) => <div data-testid="rpg">{children}</div>,
    ResizablePanel: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));

// The real Page container depends on a SidebarProvider context; mock the shell.
rs.mock("@/components/containers/page", () => {
    const Page = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
    Page.Header = ({ title, actions }: { title?: ReactNode; actions?: ReactNode }) => (
        <div>{title}{actions}</div>
    );
    return { default: Page };
});

import ReplayDebugPage from "./replay-debug";

describe("ReplayDebugPage", () => {
    afterEach(() => cleanup());

    it("renders the workflow canvas read-only on the left", () => {
        render(<ReplayDebugPage />);
        const editor = screen.getByTestId("wf-editor");
        expect(editor).toBeInTheDocument();
        expect(editor.getAttribute("data-readonly")).toBe("true");
    });

    it("mounts the replay scrubber wired to the run and the editor handle", () => {
        render(<ReplayDebugPage />);
        const scrubber = screen.getByTestId("scrubber-mock");
        expect(scrubber.getAttribute("data-runid")).toBe("run-1");
        expect(scrubber.getAttribute("data-has-editor")).toBe("true");
    });

    it("shows the workflow name and a read-only badge", () => {
        render(<ReplayDebugPage />);
        expect(screen.getByText("My Workflow")).toBeInTheDocument();
        expect(screen.getByText(/read-only/i)).toBeInTheDocument();
    });

    it("navigates back to history when Back is clicked", async () => {
        const user = userEvent.setup();
        render(<ReplayDebugPage />);
        await user.click(screen.getByTestId("replay-back"));
        expect(mockNavigate).toHaveBeenCalledWith(-1);
    });
});
