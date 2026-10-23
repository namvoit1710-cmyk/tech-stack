import { afterEach, describe, expect, it, rs } from "@rstest/core"
import { cleanup, render, screen } from "@testing-library/react"

rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }))
rs.mock("@ldc/ui", () => ({
  useTheme: () => ({ mode: "system", setMode: rs.fn() }),
  cn: (...a: unknown[]) => a.filter(Boolean).join(" "),
}))
rs.mock("@/components/containers/page", () => {
  const P = ({ children }: { children: React.ReactNode }) => <div>{children}</div>
  ;(P as unknown as { Header: () => null }).Header = () => null
  return { default: P }
})

import SettingsPage from "./settings"

afterEach(cleanup)

describe("SettingsPage", () => {
  // Credentials moved to their own /credentials page — Settings is Appearance-only now.
  it("renders appearance settings with no Credentials tab", () => {
    render(<SettingsPage />)
    // No tab strip remains.
    expect(screen.queryAllByRole("tab")).toHaveLength(0)
    // The credentials manager is not mounted here anymore.
    expect(screen.queryByTestId("creds-tab")).toBeNull()
    // Appearance settings still render (theme radiogroup present).
    expect(screen.getByRole("radiogroup")).toBeTruthy()
  })
})
