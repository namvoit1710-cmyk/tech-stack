import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core"
import { QueryClient, QueryClientProvider } from "@ldc/tanstack-query"
import type { ReactElement } from "react"
import ExecutionLogSidebar from "./execution-log-sidebar"

const mockUseExecutionLogDetail = rs.fn()
const mockMutate = rs.fn()
const mockNavigate = rs.fn()

// The sidebar reads the execution log + calls run/stop mutations + useQueryClient.
// Mock the hooks module fully and provide a QueryClient so the component renders.
rs.mock("../../hooks/apis/workflows", () => ({
    useExecutionLogDetail: (runId: string) => mockUseExecutionLogDetail(runId),
    useRunWorkflow: () => ({ mutate: mockMutate, isPending: false }),
    useStopWorkflow: () => ({ mutate: mockMutate, isPending: false }),
    workflowKey: { all: ["workflows"] },
}))

// The Replay button navigates to the read-only debug route.
rs.mock("react-router-dom", () => ({
    useNavigate: () => mockNavigate,
}))

rs.mock("@/hooks/use-language", () => ({
    useLanguage: () => ({ t: (key: string) => key }),
}))

const mockOnClose = rs.fn()

const renderSidebar = (element: ReactElement) => {
    const client = new QueryClient()
    return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)
}

const successResponse = {
    data: {
        summary: {
            run_id: "run-1",
            workflow_id: "wf-1",
            status: "COMPLETED",
            started_at: "2026-01-01T00:00:00Z",
            completed_at: "2026-01-01T00:01:00Z",
            run_ids: ["run-1"],
            total_runs: 1,
            total_events: 3,
            node_counts: { completed: 1, failed: 0, skipped: 0 },
        },
        events: [
            { index: 2, event_id: "evt-3", event_type: "NodeCompleted", topic: "node.completed", timestamp: "2026-01-01T00:00:05Z", run_id: "run-1", depth: 0, sequence: 3, causation_id: "", payload: {} },
            { index: 0, event_id: "evt-1", event_type: "RunStatusChanged", topic: "run.status", timestamp: "2026-01-01T00:00:01Z", run_id: "run-1", depth: 0, sequence: 1, causation_id: "", payload: {} },
            { index: 1, event_id: "evt-2", event_type: "NodeInput", topic: "node.input", timestamp: "2026-01-01T00:00:03Z", run_id: "run-1", depth: 0, sequence: 2, causation_id: "", payload: {} },
        ],
    },
}

describe("ExecutionLogSidebar", () => {
    beforeEach(() => {
        mockUseExecutionLogDetail.mockReset()
        mockOnClose.mockReset()
        mockNavigate.mockReset()
    })

    afterEach(() => {
        cleanup()
    })

    it("shows skeleton placeholders while loading", () => {
        mockUseExecutionLogDetail.mockReturnValue({ data: undefined, isLoading: true, error: null })

        const { container } = renderSidebar(<ExecutionLogSidebar runId="run-1" onClose={mockOnClose} />)

        const skeletons = container.querySelectorAll('[data-slot="skeleton"]')
        expect(skeletons.length).toBeGreaterThan(0)
    })

    it("renders error message when query fails", () => {
        mockUseExecutionLogDetail.mockReturnValue({
            data: undefined,
            isLoading: false,
            error: new Error("Network failure"),
        })

        renderSidebar(<ExecutionLogSidebar runId="run-1" onClose={mockOnClose} />)

        expect(screen.getByText("execution_log_load_error_title")).toBeInTheDocument()
    })

    it("renders summary and events sorted by index on success", () => {
        mockUseExecutionLogDetail.mockReturnValue({
            data: successResponse,
            isLoading: false,
            error: null,
        })

        renderSidebar(<ExecutionLogSidebar runId="run-1" onClose={mockOnClose} />)

        expect(screen.getByText("execution_log_detail_title")).toBeInTheDocument()
        expect(screen.getByText(/execution_log_summary_status/)).toBeInTheDocument()

        const eventTypes = screen.getAllByText(/RunStatusChanged|NodeInput|NodeCompleted/)
        expect(eventTypes[0]).toHaveTextContent("RunStatusChanged")
        expect(eventTypes[1]).toHaveTextContent("NodeInput")
        expect(eventTypes[2]).toHaveTextContent("NodeCompleted")
    })

    it("shows no events message when events array is empty", () => {
        mockUseExecutionLogDetail.mockReturnValue({
            data: {
                data: {
                    summary: successResponse.data.summary,
                    events: [],
                },
            },
            isLoading: false,
            error: null,
        })

        renderSidebar(<ExecutionLogSidebar runId="run-1" onClose={mockOnClose} />)

        expect(screen.getByText("execution_log_no_events")).toBeInTheDocument()
    })

    it("shows a Replay entry point for a finished run", () => {
        mockUseExecutionLogDetail.mockReturnValue({
            data: successResponse,
            isLoading: false,
            error: null,
        })

        renderSidebar(<ExecutionLogSidebar runId="run-1" onClose={mockOnClose} />)

        expect(screen.getByRole("button", { name: /Replay/i })).toBeInTheDocument()
    })

    it("navigates to the read-only replay debug route on Replay click", async () => {
        mockUseExecutionLogDetail.mockReturnValue({
            data: successResponse,
            isLoading: false,
            error: null,
        })

        const user = userEvent.setup()
        renderSidebar(<ExecutionLogSidebar runId="run-1" onClose={mockOnClose} />)

        await user.click(screen.getByRole("button", { name: /Replay/i }))
        expect(mockNavigate).toHaveBeenCalledWith("/workflow/wf-1/replay/run-1")
    })

    it("calls onClose when close button is clicked", async () => {
        mockUseExecutionLogDetail.mockReturnValue({
            data: successResponse,
            isLoading: false,
            error: null,
        })

        const user = userEvent.setup()
        renderSidebar(<ExecutionLogSidebar runId="run-1" onClose={mockOnClose} />)

        const closeButton = screen.getByRole("button", { name: "" })
        expect(closeButton).toBeInTheDocument()

        await user.click(closeButton)
        expect(mockOnClose).toHaveBeenCalledTimes(1)
    })
})
