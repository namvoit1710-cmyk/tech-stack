import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, rs } from "@rstest/core"
import { MemoryRouter } from "react-router-dom"
import ExecutionRunRow, { type IRunRow } from "./execution-run-row"

const mockSetSelectedRunId = rs.fn()
const mockSetIsLogSidebarOpen = rs.fn()

rs.mock("@/hooks/use-language", () => ({
    useLanguage: () => ({ t: (key: string) => key }),
}))

rs.mock("../../stores/ui-panel-stores", () => ({
    useUIPanelStore: (selector: (s: Record<string, unknown>) => unknown) =>
        selector({
            selectedRunId: null,
            setSelectedRunId: mockSetSelectedRunId,
            setIsLogSidebarOpen: mockSetIsLogSidebarOpen,
        }),
}))

rs.mock("../../hooks/apis/workflows", () => ({
    useRunChildren: () => ({ data: undefined, isLoading: false }),
    useRunRating: () => ({ data: undefined }),
    useRunRatings: () => ({ data: {} }),
    useRateRun: () => ({ mutate: rs.fn(), isPending: false }),
    workflowKey: { ratings: () => ["ratings"] },
}))

rs.mock("@ldc/tanstack-query", () => ({
    useQueryClient: () => ({ invalidateQueries: rs.fn() }),
}))

rs.mock("@ldc/ui/blocks/toast/toast", () => ({ toast: { success: rs.fn(), error: rs.fn() } }))

const baseRun: IRunRow = {
    id: "run-1",
    workflow_id: "wf-1",
    workflow_name: "My flow",
    status: "COMPLETED",
    started_at: "2026-09-29T03:00:00Z",
    completed_at: "2026-09-29T03:00:10Z",
}

const renderRow = (run: IRunRow) =>
    render(
        <MemoryRouter>
            <ExecutionRunRow run={run} />
        </MemoryRouter>
    )

describe("ExecutionRunRow — SA-2848 open workflow in new tab", () => {
    afterEach(() => {
        cleanup()
        mockSetSelectedRunId.mockReset()
        mockSetIsLogSidebarOpen.mockReset()
    })

    it("renders a new-tab link to the run's own workflow", () => {
        renderRow(baseRun)
        const link = screen.getByRole("link", { name: "open_workflow_new_tab" })
        expect(link.getAttribute("href")).toBe("/workflow/wf-1")
        expect(link.getAttribute("target")).toBe("_blank")
        expect(link.getAttribute("rel")).toBe("noopener noreferrer")
    })

    it("uses a sub-run's own workflow_id", () => {
        renderRow({ ...baseRun, id: "child-1", workflow_id: "sub-wf", parent_run_id: "run-1" })
        expect(screen.getByRole("link", { name: "open_workflow_new_tab" }).getAttribute("href")).toBe("/workflow/sub-wf")
    })

    it("does not open the log sidebar when clicked", () => {
        renderRow(baseRun)
        fireEvent.click(screen.getByRole("link", { name: "open_workflow_new_tab" }))
        expect(mockSetIsLogSidebarOpen).not.toHaveBeenCalled()
        expect(mockSetSelectedRunId).not.toHaveBeenCalled()
    })

    it("hides the link when the run has no workflow_id", () => {
        renderRow({ ...baseRun, workflow_id: undefined })
        expect(screen.queryByRole("link", { name: "open_workflow_new_tab" })).toBeNull()
    })
})

describe("ExecutionRunRow — SA-2876 run star", () => {
    afterEach(() => cleanup())

    it("top-level run shows the Star button", () => {
        renderRow(baseRun)
        expect(screen.getByRole("button", { name: "star_run" })).toBeTruthy()
    })

    it("sub-run has no Star button", () => {
        renderRow({ ...baseRun, id: "child-1", parent_run_id: "run-1" })
        expect(screen.queryByRole("button", { name: "star_run" })).toBeNull()
    })
})
