import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

let configured = true
const useCredentialsMock = rs.fn()
const testMutate = rs.fn()
const deleteMutate = rs.fn()
const updateMutate = rs.fn()
const navigateMock = rs.fn()

rs.mock("@/lib/credentials-api", () => ({ isCredentialsConfigured: () => configured }))
rs.mock("@/features/workflows/hooks/apis/credentials", () => ({
  credentialKeys: { list: () => ["credentials", "list"] },
  useCredentials: () => useCredentialsMock(),
  useTestCredential: () => ({ mutate: testMutate, data: undefined, isPending: false }),
  useDeleteCredential: () => ({ mutate: deleteMutate, isPending: false }),
  useUpdateCredential: () => ({ mutate: updateMutate, isPending: false }),
}))
rs.mock("react-router-dom", () => ({ useNavigate: () => navigateMock }))
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }))
rs.mock("@ldc/tanstack-query", () => ({ useQueryClient: () => ({ invalidateQueries: rs.fn() }) }))
// Isolate from the add-form's own hooks — its behaviour is covered elsewhere.
rs.mock("@/features/workflows/components/node-config-controls/credential-setup-dialog", () => ({
  CredentialSetupDialog: ({ open }: { open: boolean }) => (open ? <div data-testid="setup-dialog" /> : null),
}))

import CredentialsSettings from "./credentials-settings"

// The Add button now lives in the page's Page.Header; this list component is controlled
// via addOpen/onAddOpenChange. Render helper supplies the required props.
const renderList = (addOpen = false) =>
  render(<CredentialsSettings addOpen={addOpen} onAddOpenChange={() => {}} />)

const cred = {
  id: "cred_1", typeId: "generic_api_key", displayName: "My CRM Key",
  provider: "GENERIC_HTTP", credentialKind: "API_KEY", status: "ACTIVE", expiresAt: "2027-01-01T00:00:00Z",
}
const teams = {
  id: "cred_2", typeId: "teams_webhook", displayName: "Eng Webhook",
  provider: "MICROSOFT_TEAMS", credentialKind: "webhook_url", status: "EXPIRING_SOON", expiresAt: null,
}

beforeEach(() => { configured = true })
afterEach(() => {
  cleanup()
  useCredentialsMock.mockReset()
  testMutate.mockReset()
  deleteMutate.mockReset()
  updateMutate.mockReset()
  navigateMock.mockReset()
})

describe("CredentialsSettings", () => {
  it("shows a not-configured notice and no table when the vault is unconfigured", () => {
    configured = false
    useCredentialsMock.mockReturnValue({ data: undefined, isLoading: false })
    renderList()
    expect(screen.getByTestId("credentials-not-configured")).toBeTruthy()
    expect(screen.queryByTestId("credentials-table-body")).toBeNull()
  })

  it("lists every vault credential and never leaks a secret", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred, teams] }, isLoading: false })
    const { container } = renderList()
    expect(screen.getByTestId("credentials-table-body").querySelectorAll("tr")).toHaveLength(2)
    expect(screen.getByText("My CRM Key")).toBeTruthy()
    expect(screen.getByText("Eng Webhook")).toBeTruthy()
    expect(screen.getByText("MICROSOFT_TEAMS")).toBeTruthy()
    expect(container.querySelector('input[type="password"]')).toBeNull()
    expect(container.innerHTML).not.toMatch(/secret/i)
  })

  it("shows the empty state when configured but no credentials exist", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [] }, isLoading: false })
    renderList()
    expect(screen.getByTestId("credentials-empty")).toBeTruthy()
  })

  it("tests a credential row against the vault", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred] }, isLoading: false })
    renderList()
    fireEvent.click(screen.getByTestId("credentials-test-cred_1"))
    expect(testMutate).toHaveBeenCalledWith("cred_1")
  })

  it("renders the add dialog when the page opens it (controlled)", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [] }, isLoading: false })
    const { rerender } = renderList(false)
    expect(screen.queryByTestId("setup-dialog")).toBeNull()
    rerender(<CredentialsSettings addOpen={true} onAddOpenChange={() => {}} />)
    expect(screen.getByTestId("setup-dialog")).toBeTruthy()
  })

  it("navigates to the edit route for a row", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred] }, isLoading: false })
    renderList()
    fireEvent.click(screen.getByTestId("credentials-edit-cred_1"))
    expect(navigateMock).toHaveBeenCalledWith("/credentials/cred_1/edit")
  })

  it("requires a confirm click before deleting", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred] }, isLoading: false })
    renderList()
    // First click reveals the confirm button; it does NOT delete yet.
    fireEvent.click(screen.getByTestId("credentials-delete-cred_1"))
    expect(deleteMutate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId("credentials-delete-confirm-cred_1"))
    expect(deleteMutate).toHaveBeenCalledWith("cred_1", expect.anything())
  })
})


// SA-2578: the tab must scroll. The list body is the scroll region (flex-1 +
// min-h-0 + overflow-auto) inside the overflow-hidden Page shell, and the column
// header sticks so it stays visible. jsdom has no layout, so we assert the
// contract at the DOM/class level plus a long-list render guard.
describe("CredentialsSettings scroll (SA-2578)", () => {
  const many = Array.from({ length: 40 }, (_, i) => ({
    ...cred,
    id: `cred_${i}`,
    displayName: `Credential ${i}`,
  }))

  it("makes the list body a vertical scroll region (UC-1, UC-2)", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: many }, isLoading: false })
    const { container } = renderList()
    const section = container.querySelector("section") as HTMLElement
    // flex-1 lets it fill the Page shell; min-h-0 lets it shrink below content;
    // overflow-auto gives it the scrollbar so nothing is clipped.
    expect(section.className).toContain("flex-1")
    expect(section.className).toContain("min-h-0")
    expect(section.className).toContain("overflow-auto")
  })

  it("renders every credential in a long list — none are dropped (UC-1)", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: many }, isLoading: false })
    renderList()
    const rows = screen.getByTestId("credentials-table-body").querySelectorAll("tr")
    expect(rows).toHaveLength(40)
    // The last row (below the fold) is present and its actions are reachable.
    expect(screen.getByText("Credential 39")).toBeTruthy()
    expect(screen.getByTestId("credentials-edit-cred_39")).toBeTruthy()
  })

  it("keeps the column header visible via a sticky thead (UC-3)", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: many }, isLoading: false })
    const { container } = renderList()
    const thead = container.querySelector("thead") as HTMLElement
    expect(thead.className).toContain("sticky")
    expect(thead.className).toContain("top-0")
    // Opaque background so scrolled rows do not bleed through the header.
    expect(thead.className).toContain("bg-muted")
    expect(thead.className).not.toContain("bg-muted/50")
  })

  it("adds no scroll chrome for the empty state (UC-4 regression)", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [] }, isLoading: false })
    renderList()
    // Empty state renders its message, not a table.
    expect(screen.getByTestId("credentials-empty")).toBeTruthy()
    expect(screen.queryByTestId("credentials-table-body")).toBeNull()
  })
})

// SA-2279: the automation grant. Event- and schedule-triggered runs have no user
// behind them, so the vault refuses a user-owned credential to them unless its
// owner grants it here. This toggle is the only way to set that.
describe("CredentialsSettings automation grant", () => {
  it("reflects the stored grant rather than defaulting to on", () => {
    useCredentialsMock.mockReturnValue({
      data: { credentials: [{ ...cred, automationAllowed: false }, { ...teams, automationAllowed: true }] },
      isLoading: false,
    })
    renderList()

    expect((screen.getByTestId("credentials-automation-cred_1") as HTMLButtonElement)
      .getAttribute("aria-checked")).toBe("false")
    expect((screen.getByTestId("credentials-automation-cred_2") as HTMLButtonElement)
      .getAttribute("aria-checked")).toBe("true")
  })

  it("treats a missing field as NOT granted", () => {
    // An older backend, or a response that predates the column, must never
    // render as granted — that would tell the user their secret is reachable
    // by automation when it is not, or worse, the reverse.
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred] }, isLoading: false })
    renderList()
    expect((screen.getByTestId("credentials-automation-cred_1") as HTMLButtonElement)
      .getAttribute("aria-checked")).toBe("false")
  })

  it("sends ONLY the grant, with the row version as If-Match", () => {
    useCredentialsMock.mockReturnValue({
      data: { credentials: [{ ...cred, automationAllowed: false, version: 7 }] },
      isLoading: false,
    })
    renderList()

    fireEvent.click(screen.getByTestId("credentials-automation-cred_1"))

    expect(updateMutate).toHaveBeenCalledTimes(1)
    const [payload] = updateMutate.mock.calls[0]
    // Carrying displayName/formData too would re-validate and re-encrypt the
    // secret — not what flipping a switch should do.
    expect(payload.body).toEqual({ automationAllowed: true })
    expect(payload.credentialId).toBe("cred_1")
    expect(payload.version).toBe(7)
  })

  it("can withdraw a grant", () => {
    useCredentialsMock.mockReturnValue({
      data: { credentials: [{ ...cred, automationAllowed: true, version: 2 }] },
      isLoading: false,
    })
    renderList()
    fireEvent.click(screen.getByTestId("credentials-automation-cred_1"))
    expect(updateMutate.mock.calls[0][0].body).toEqual({ automationAllowed: false })
  })

  it("is disabled for a revoked credential", () => {
    useCredentialsMock.mockReturnValue({
      data: { credentials: [{ ...cred, status: "REVOKED", automationAllowed: false }] },
      isLoading: false,
    })
    renderList()
    expect(screen.getByTestId("credentials-automation-cred_1")).toBeDisabled()
  })
})
