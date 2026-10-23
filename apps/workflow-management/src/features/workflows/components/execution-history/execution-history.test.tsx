import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core"
import WorkflowExecutionHistory from "./execution-history"

const mockSetCurrentPage = rs.fn()
const mockSetSelectedRunId = rs.fn()
const mockSetIsLogSidebarOpen = rs.fn()

let mockIsLogSidebarOpen = false
let mockSelectedRunId: string | null = null

rs.mock("@/hooks/use-language", () => ({
    useLanguage: () => ({ t: (key: string) => key }),
}))

rs.mock("../../hooks/use-search-params-query", () => ({
    default: () => ({
        currentPage: 1,
        setCurrentPage: mockSetCurrentPage,
        workflowId: "wf-1",
        setWorkflowId: rs.fn(),
        executionStatus: undefined,
        setExecutionStatus: rs.fn(),
        sorting: [{ id: "started_at", desc: true }],
        setSorting: rs.fn(),
    }),
}))

rs.mock("../../stores/ui-panel-stores", () => ({
    useUIPanelStore: (selector: (s: Record<string, unknown>) => unknown) =>
        selector({
            isLogSidebarOpen: mockIsLogSidebarOpen,
            selectedRunId: mockSelectedRunId,
            setIsLogSidebarOpen: mockSetIsLogSidebarOpen,
            setSelectedRunId: mockSetSelectedRunId,
        }),
}))

const mockUseWorkflowExecutionList = rs.fn()

rs.mock("../../hooks/apis/workflows", () => ({
    useWorkflowExecutionList: (...args: unknown[]) => mockUseWorkflowExecutionList(...args),
    // SA-2825: the list now batches per-run ratings in one call; stub it empty so
    // the container test stays focused on layout/empty-state/pagination.
    useRunRatings: () => ({ data: {} }),
    keepPreviousData: (data: unknown) => data,
}))

// The realtime socket hook isn't under test here (and its module-load pulls
// workflowKey, which this file's workflows mock doesn't expose) — stub it no-op.
rs.mock("../../hooks/use-execution-history-realtime", () => ({
    useExecutionHistoryRealtime: () => {},
}))

rs.mock("@ldc/ui", () => ({
    cn: (...args: unknown[]) => args.filter(Boolean).join(" "),
}))

rs.mock("@ldc/ui/components/resizable", () => ({
    ResizablePanelGroup: ({ children, className }: any) => (
        <div data-testid="resizable-group" className={className}>{children}</div>
    ),
    ResizablePanel: ({ children }: any) => <div data-testid="resizable-panel">{children}</div>,
    ResizableHandle: () => <div data-testid="resizable-handle" />,
}))

rs.mock("./execution-filter", () => ({
    default: () => <div data-testid="execution-filter" />,
}))

rs.mock("./execution-log-sidebar", () => ({
    default: ({ runId, onClose }: any) => (
        <div data-testid="execution-log-sidebar" data-run-id={runId}>
            <button data-testid="sidebar-close" onClick={onClose} />
        </div>
    ),
}))

// The row (incl. its lazy sub-run loading) is covered separately; stub it here so
// the container test stays focused on layout, the empty state, and pagination.
rs.mock("./execution-run-row", () => ({
    default: ({ run }: any) => <div data-testid="run-row" data-run-id={run.id}>{run.workflow_name ?? run.id}</div>,
}))

rs.mock("../common/list-pager", () => ({
    ListPager: ({ page, onChange }: any) => (
        <button data-testid="next-page" onClick={() => onChange(page + 1)} />
    ),
}))

const mockExecutionItems = [
    { id: "run-1", workflow_id: "wf-1", workflow_name: "Daily Sync", status: "COMPLETED", started_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:01:00Z" },
    { id: "run-2", workflow_id: "wf-1", workflow_name: "Nightly ETL", status: "FAILED", started_at: "2026-01-02T00:00:00Z", completed_at: "2026-01-02T00:00:30Z" },
]

describe("WorkflowExecutionHistory", () => {
    beforeEach(() => {
        mockUseWorkflowExecutionList.mockReset()
        mockSetCurrentPage.mockReset()
        mockSetSelectedRunId.mockReset()
        mockSetIsLogSidebarOpen.mockReset()
        mockIsLogSidebarOpen = false
        mockSelectedRunId = null

        mockUseWorkflowExecutionList.mockReturnValue({
            data: { data: { items: mockExecutionItems, total: 2 } },
            isLoading: false,
            isFetching: false,
        })
    })

    afterEach(() => {
        cleanup()
    })

    it("requests root runs only and renders one row per run", () => {
        render(<WorkflowExecutionHistory isActive />)

        expect(mockUseWorkflowExecutionList).toHaveBeenCalledWith(
            expect.objectContaining({ roots_only: true }),
            expect.anything(),
        )
        expect(screen.getByTestId("execution-filter")).toBeInTheDocument()
        expect(screen.getByTestId("resizable-group")).toBeInTheDocument()
        expect(screen.getAllByTestId("run-row")).toHaveLength(2)
        expect(screen.getByText("Daily Sync")).toBeInTheDocument()
        expect(screen.getByText("Nightly ETL")).toBeInTheDocument()
    })

    it("renders the empty state when there are no runs", () => {
        mockUseWorkflowExecutionList.mockReturnValue({
            data: { data: { items: [], total: 0 } },
            isLoading: false,
            isFetching: false,
        })

        render(<WorkflowExecutionHistory isActive />)
        expect(screen.queryByTestId("run-row")).toBeNull()
        expect(screen.getByText("no_executions")).toBeInTheDocument()
    })

    it("renders ExecutionLogSidebar when sidebar is open", () => {
        mockIsLogSidebarOpen = true
        mockSelectedRunId = "run-1"

        render(<WorkflowExecutionHistory isActive />)

        const sidebar = screen.getByTestId("execution-log-sidebar")
        expect(sidebar).toBeInTheDocument()
        expect(sidebar).toHaveAttribute("data-run-id", "run-1")
    })

    it("does not render ExecutionLogSidebar when closed", () => {
        render(<WorkflowExecutionHistory isActive />)
        expect(screen.queryByTestId("execution-log-sidebar")).toBeNull()
    })

    it("shows loader bar on a user-initiated fetch (page/filter change)", () => {
        // SA-2650: isPlaceholderData marks a new-queryKey fetch (page/filter),
        // for which we do show the loader.
        mockUseWorkflowExecutionList.mockReturnValue({
            data: { data: { items: mockExecutionItems, total: 2 } },
            isLoading: false,
            isFetching: true,
            isPlaceholderData: true,
        })

        const { container } = render(<WorkflowExecutionHistory isActive />)
        expect(container.querySelector(".loader-bar")).toBeInTheDocument()
    })

    it("hides the loader bar during a silent background poll", () => {
        // SA-2650: the 4s auto-refresh (same queryKey → isPlaceholderData false)
        // must update silently so the progress chips don't flicker every tick.
        mockUseWorkflowExecutionList.mockReturnValue({
            data: { data: { items: mockExecutionItems, total: 2 } },
            isLoading: false,
            isFetching: true,
            isPlaceholderData: false,
        })

        const { container } = render(<WorkflowExecutionHistory isActive />)
        expect(container.querySelector(".loader-bar")).toBeNull()
    })

    it("pagination change calls setCurrentPage with next page", async () => {
        const user = userEvent.setup()
        render(<WorkflowExecutionHistory isActive />)

        await user.click(screen.getByTestId("next-page"))
        expect(mockSetCurrentPage).toHaveBeenCalledWith(2)
    })
})
