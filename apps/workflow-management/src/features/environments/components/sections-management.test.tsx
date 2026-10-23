import { afterEach, describe, expect, it, rs } from "@rstest/core"
import { cleanup, render, screen } from "@testing-library/react"

// SA-2904 UC4 — "Workflows bound to {section}" lists every bound flow from ONE call
// (GET /sections/{section}/workflows), not the first page (20) of /workflows.
const graphs: Record<string, any> = {
  "wf-create-template": { id: "wf-create-template", name: "[MAINFLOW] Create Template",
    nodes: [{ id: "n1", type: "WORKFLOW", parameters: { workflow_id: "wf-sub" } }] },
  "wf-sub": { id: "wf-sub", name: "Sub flow", nodes: [] },
}
let boundIds: string[] = []
const listInfinite = rs.fn()

rs.mock("@/features/environments/hooks/apis/environments", () => ({
  useSectionWorkflows: (section: string) => ({ data: { data: { section, workflow_ids: boundIds } }, isLoading: false }),
}))
rs.mock("@/features/workflows/hooks/apis/workflows", () => ({
  workflowKey: { getWorkflowById: (id: string) => ["workflows", id] },
  useWorkflowListInfinite: (...a: unknown[]) => listInfinite(...a),
}))
rs.mock("@ldc/tanstack-query", () => ({
  useQueryClient: () => ({ invalidateQueries: rs.fn() }),
  useQueries: ({ queries }: { queries: { queryKey: string[] }[] }) =>
    queries.map(q => { const g = graphs[q.queryKey[1] ?? ""]; return { data: g ? { data: g } : undefined, isLoading: false } }),
}))
rs.mock("@/lib/api", () => ({ default: { get: rs.fn(), put: rs.fn() } }))
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }))

import { BoundWorkflows } from "./sections-management"

afterEach(cleanup)

describe("BoundWorkflows (SA-2904 UC4)", () => {
  it("lists every bound flow from the section endpoint, with role chips", () => {
    boundIds = ["wf-create-template", "wf-sub"]
    render(<BoundWorkflows section="SMDG_AI" />)
    expect(screen.getByText("[MAINFLOW] Create Template")).toBeTruthy()
    expect(screen.getByTestId("bound-role-parent-wf-create-template")).toBeTruthy()
    expect(screen.getByTestId("bound-role-subflow-wf-sub")).toBeTruthy()
    expect(listInfinite).not.toHaveBeenCalled() // no longer pages /workflows
  })

  it("skips a bound id whose workflow no longer loads, and shows empty state", () => {
    boundIds = ["wf-deleted"]
    render(<BoundWorkflows section="EMPTY" />)
    expect(screen.getByText("environments.section_no_workflows")).toBeTruthy()
  })
})
