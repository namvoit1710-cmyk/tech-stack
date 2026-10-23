import { afterEach, describe, expect, it, rs } from "@rstest/core"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

// Stub the add-form: this is a unit test of the dialog wrapper (open/close + bubbling),
// not of the form internals (covered in credential-add-form.test.tsx).
rs.mock("./credential-add-form", () => ({
  CredentialAddForm: ({ onCreated, onCancel }: { onCreated: (id: string) => void; onCancel: () => void }) => (
    <div data-testid="add-form-stub">
      <button data-testid="stub-create" type="button" onClick={() => onCreated("cred_new")}>
        create
      </button>
      <button data-testid="stub-cancel" type="button" onClick={onCancel}>
        cancel
      </button>
    </div>
  ),
}))
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }))

import { CredentialSetupDialog } from "./credential-setup-dialog"

afterEach(cleanup)

describe("CredentialSetupDialog", () => {
  it("renders the add form inside the dialog when open", () => {
    render(<CredentialSetupDialog open onOpenChange={() => {}} onCreated={() => {}} />)
    expect(screen.getByTestId("add-form-stub")).toBeTruthy()
  })

  it("renders nothing when closed", () => {
    render(<CredentialSetupDialog open={false} onOpenChange={() => {}} onCreated={() => {}} />)
    expect(screen.queryByTestId("add-form-stub")).toBeNull()
  })

  it("bubbles the new id and closes on create", () => {
    const onCreated = rs.fn()
    const onOpenChange = rs.fn()
    render(<CredentialSetupDialog open onOpenChange={onOpenChange} onCreated={onCreated} />)
    fireEvent.click(screen.getByTestId("stub-create"))
    expect(onCreated).toHaveBeenCalledWith("cred_new")
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("closes on cancel without creating", () => {
    const onCreated = rs.fn()
    const onOpenChange = rs.fn()
    render(<CredentialSetupDialog open onOpenChange={onOpenChange} onCreated={onCreated} />)
    fireEvent.click(screen.getByTestId("stub-cancel"))
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(onCreated).not.toHaveBeenCalled()
  })
})
