import { afterEach, beforeAll, describe, expect, it, rs } from "@rstest/core"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

// The picker is a Radix Select now (SimpleSelect), not a native <select>: its list
// is portalled on open, and jsdom implements neither pointer capture nor
// scrollIntoView. See simple-select.test.tsx for the same recipe.
beforeAll(() => {
  Element.prototype.scrollIntoView = rs.fn() as unknown as Element["scrollIntoView"]
  Element.prototype.hasPointerCapture = (() => false) as Element["hasPointerCapture"]
  Element.prototype.setPointerCapture = (() => undefined) as Element["setPointerCapture"]
  Element.prototype.releasePointerCapture = (() => undefined) as Element["releasePointerCapture"]
})

/** Radix opens on ArrowDown — no pointer APIs involved. */
const openPicker = () => fireEvent.keyDown(screen.getByTestId("credential-picker-select"), { key: "ArrowDown" })
/** Items commit on their own keydown for Enter/Space; pointerup is guarded against
 *  the opening gesture and so never fires in jsdom. */
const choose = (label: string) => fireEvent.keyDown(screen.getByRole("option", { name: label }), { key: "Enter" })

const useCredentialsMock = rs.fn()
const testMutate = rs.fn()
const testReset = rs.fn()
let testData: { valid: boolean; error?: string } | undefined

rs.mock("@/features/workflows/hooks/apis/credentials", () => ({
  useCredentials: () => useCredentialsMock(),
  useTestCredential: () => ({ mutate: testMutate, data: testData, isPending: false, reset: testReset }),
  useCredentialTypes: () => ({ data: { types: [] }, isLoading: false }),
  useIssueCredential: () => ({ mutateAsync: rs.fn(), isPending: false }),
  useTestInlineCredential: () => ({ mutateAsync: rs.fn(), isPending: false }),
}))
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }))

import { CredentialPickerControl } from "./credential-picker-control"

const cred = {
  id: "cred_123", typeId: "generic_api_key", displayName: "My CRM API Key",
  provider: "GENERIC_HTTP", credentialKind: "API_KEY", status: "ACTIVE",
}
const teamsCred = {
  id: "cred_teams", typeId: "teams_webhook", displayName: "Eng Channel Webhook",
  provider: "MICROSOFT_TEAMS", credentialKind: "webhook_url", status: "ACTIVE",
}

afterEach(() => { cleanup(); useCredentialsMock.mockReset(); testMutate.mockReset(); testReset.mockReset(); testData = undefined })

describe("CredentialPickerControl", () => {
  it("lists only credentials matching the node's provider + kinds", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred, teamsCred] }, isLoading: false })
    render(<CredentialPickerControl name="credential_ref" value="" onChange={() => {}} provider="GENERIC_HTTP" kinds={["API_KEY"]} />)
    openPicker()
    expect(screen.getByRole("option", { name: "My CRM API Key" })).toBeTruthy()
    expect(screen.queryByRole("option", { name: "Eng Channel Webhook" })).toBeNull()
  })

  it("emits a {target:{name,value}} envelope with the credential id on select", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred] }, isLoading: false })
    const onChange = rs.fn()
    render(<CredentialPickerControl name="credential_ref" value="" onChange={onChange} provider="GENERIC_HTTP" kinds={["API_KEY"]} />)
    openPicker()
    choose("My CRM API Key")
    expect(onChange).toHaveBeenCalledWith({ target: { name: "credential_ref", value: "cred_123" } })
  })

  it("renders the selected credential summary + status badge and never a secret", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred] }, isLoading: false })
    const { container } = render(<CredentialPickerControl name="credential_ref" value="cred_123" onChange={() => {}} provider="GENERIC_HTTP" kinds={["API_KEY"]} />)
    expect(screen.getByTestId("credential-summary")).toBeTruthy()
    expect(screen.getByText("ACTIVE")).toBeTruthy()
    expect(container.querySelector('input[type="password"]')).toBeNull()
    expect(container.innerHTML).not.toMatch(/secret/i)
  })

  it("warns to pick another when the stored ref is no longer available", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [] }, isLoading: false })
    render(<CredentialPickerControl name="credential_ref" value="cred_gone" onChange={() => {}} provider="GENERIC_HTTP" kinds={["API_KEY"]} />)
    expect(screen.getByText("nodes.credential.pick_another")).toBeTruthy()
  })

  it("tests the selected credential against the vault", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred] }, isLoading: false })
    render(<CredentialPickerControl name="credential_ref" value="cred_123" onChange={() => {}} provider="GENERIC_HTTP" kinds={["API_KEY"]} />)
    fireEvent.click(screen.getByText("nodes.credential.test"))
    expect(testMutate).toHaveBeenCalledWith("cred_123")
  })

  it("shows a green result when the test passes", () => {
    testData = { valid: true }
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred] }, isLoading: false })
    render(<CredentialPickerControl name="credential_ref" value="cred_123" onChange={() => {}} provider="GENERIC_HTTP" kinds={["API_KEY"]} />)
    expect(screen.getByTestId("credential-test-ok")).toBeTruthy()
    expect(screen.queryByTestId("credential-test-fail")).toBeNull()
  })

  it("shows a red result with the error when the test fails", () => {
    testData = { valid: false, error: "401 Unauthorized" }
    useCredentialsMock.mockReturnValue({ data: { credentials: [cred] }, isLoading: false })
    render(<CredentialPickerControl name="credential_ref" value="cred_123" onChange={() => {}} provider="GENERIC_HTTP" kinds={["API_KEY"]} />)
    expect(screen.getByTestId("credential-test-fail")).toBeTruthy()
    expect(screen.getByText("401 Unauthorized")).toBeTruthy()
  })

  it("disables the dropdown when there are no credentials to pick", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [] }, isLoading: false })
    render(<CredentialPickerControl name="credential_ref" value="" onChange={() => {}} provider="GENERIC_HTTP" kinds={["API_KEY"]} />)
    expect((screen.getByTestId("credential-picker-select") as HTMLButtonElement).disabled).toBe(true)
  })

  it("opens the setup-credential popup on demand", () => {
    useCredentialsMock.mockReturnValue({ data: { credentials: [] }, isLoading: false })
    render(<CredentialPickerControl name="credential_ref" value="" onChange={() => {}} provider="GENERIC_HTTP" kinds={["API_KEY"]} />)
    expect(screen.queryByTestId("credential-add-type")).toBeNull()
    fireEvent.click(screen.getByTestId("credential-setup-open"))
    expect(screen.getByTestId("credential-add-type")).toBeTruthy()
  })
})
