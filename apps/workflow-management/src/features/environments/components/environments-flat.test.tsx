import { afterEach, describe, expect, it, rs } from "@rstest/core"
import { cleanup, render, screen } from "@testing-library/react"

// SA-2898 — the env list + values panel must scroll inside a card that fills the tab
// height. jsdom has no layout, so this pins the class contract; live scroll is verified
// against the running FE.
const envs = Array.from({ length: 22 }, (_, i) => ({ id: `env_${i}`, name: `ENV_${i}`, variables: [] }))
const envList = { data: { data: { items: envs } }, isLoading: false }
const sectionList = { data: { data: { items: [{ section: "SMDG", keys: [{ key: "APP_URL", type: "text" }] }] } } }
const memberships = { data: { data: { memberships: [{ section: "SMDG", environment_ids: envs.map(e => e.id) }] } } }
const mutation = { mutate: rs.fn(), mutateAsync: rs.fn(), isPending: false }

rs.mock("@/features/environments/hooks/apis/environments", () => ({
  environmentKey: { all: ["environments"], list: () => ["environments", "list"] },
  envSectionKey: { all: ["env-sections"], list: () => ["env-sections", "list"] },
  useEnvironmentList: () => envList,
  useEnvSectionList: () => sectionList,
  useListSectionMemberships: () => memberships,
  useCreateEnvironment: () => mutation,
  useUpdateEnvironment: () => mutation,
  useDeleteEnvironment: () => mutation,
}))
rs.mock("@/features/workflows/hooks/apis/credentials", () => ({ useCredentials: () => ({ data: { credentials: [] } }) }))
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }))
rs.mock("@ldc/tanstack-query", () => ({ useQueryClient: () => ({ invalidateQueries: rs.fn() }) }))

import EnvironmentsFlat from "./environments-flat"

const cls = (id: string) => screen.getByTestId(id).className

afterEach(cleanup)

describe("EnvironmentsFlat scroll layout (SA-2898)", () => {
  it("UC-1: the card fills the tab height instead of a fixed 520px cap", () => {
    const { container } = render(<EnvironmentsFlat />)
    expect(cls("environments-flat")).toMatch(/\bh-full\b/)
    expect(cls("environments-flat")).toMatch(/\bmin-h-0\b/)
    expect(cls("flat-env-card")).toMatch(/\bflex-1\b/)
    expect(cls("flat-env-card")).toMatch(/\boverflow-hidden\b/)
    expect(container.innerHTML).not.toContain("max-h-[520px]")
  })

  it("UC-2: the env list scrolls inside the card and every env is reachable", () => {
    render(<EnvironmentsFlat />)
    const list = screen.getByTestId("flat-env-list-scroll")
    expect(list.className).toMatch(/\bmin-h-0\b/)
    expect(list.className).toMatch(/\bflex-1\b/)
    expect(list.parentElement?.className).toMatch(/\bmin-h-0\b/)
    expect(screen.getByTestId("flat-env-item-ENV_21")).toBeTruthy()
    // search stays outside (above) the scrolling list
    expect(list.contains(screen.getByTestId("flat-env-search"))).toBe(false)
  })

  it("UC-3: the values panel scrolls on its own with a sticky env header", () => {
    render(<EnvironmentsFlat />)
    expect(cls("flat-env-values-scroll")).toMatch(/\boverflow-y-auto\b/)
    expect(cls("flat-env-values-scroll")).toMatch(/\bmin-h-0\b/)
    const header = screen.getByTestId("flat-rename-environment-ENV_0").parentElement!
    expect(header.className).toMatch(/\bsticky\b/)
    expect(header.className).toMatch(/\btop-0\b/)
  })

  it("UC-4: the card keeps a minimum height for short windows", () => {
    render(<EnvironmentsFlat />)
    expect(cls("flat-env-card")).toMatch(/\bmin-h-90\b/)
  })
})
