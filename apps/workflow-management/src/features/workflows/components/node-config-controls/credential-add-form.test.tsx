import { afterEach, beforeAll, describe, expect, it, rs } from "@rstest/core"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"

// The type picker is a Radix Select now (SimpleSelect), not a native <select>: its
// list is portalled on open, so a type is chosen by label rather than by value, and
// jsdom implements neither pointer capture nor scrollIntoView.
beforeAll(() => {
  Element.prototype.scrollIntoView = rs.fn() as unknown as Element["scrollIntoView"]
  Element.prototype.hasPointerCapture = (() => false) as Element["hasPointerCapture"]
  Element.prototype.setPointerCapture = (() => undefined) as Element["setPointerCapture"]
  Element.prototype.releasePointerCapture = (() => undefined) as Element["releasePointerCapture"]
})

/** Radix opens on ArrowDown. The list is portalled on demand, so it does not exist
 *  in the DOM until this runs — a native <select> kept its <option>s there always. */
const openTypes = () => fireEvent.keyDown(screen.getByTestId("credential-add-type"), { key: "ArrowDown" })
/** Items commit on their own Enter; pointerup is guarded against the opening
 *  gesture and never fires in jsdom. */
const pickType = (label: string) => {
  openTypes()
  fireEvent.keyDown(screen.getByRole("option", { name: label }), { key: "Enter" })
}

const issueMutate = rs.fn(() => Promise.resolve({ credentialId: "cred_new" }))
let testInlineResult: { valid: boolean; error?: string | null; latencyMs?: number } = { valid: true, latencyMs: 42 }
const testInlineMutate = rs.fn((_b?: unknown) => Promise.resolve(testInlineResult))
let encAvailable = false
const encryptMock = rs.fn((_d?: Record<string, unknown>) => Promise.resolve({ key: "K", iv: "IV", ciphertext: "CT" }))

// A flat schema with the real submit_credential mapping: displayName + one data field.
// The form flattens the Builder's nested values via this mapping (same as the edit page).
const apiKeySchema = {
  fields: [
    {
      key: "credentialForm",
      outputType: "object",
      fieldConfig: {
        fieldWrapper: "DivWrapper",
        wrapperProps: {
          actions: [
            {
              action: "submit_credential",
              steps: [
                {
                  type: "api_call",
                  body: {
                    typeId: "generic_api_key",
                    displayName: "${formValues.displayName}",
                    formData: { value: "${formValues.value}" },
                  },
                },
              ],
            },
          ],
        },
      },
      fields: [
        { key: "displayName", fieldConfig: { fieldControl: "InputControl" } },
        { key: "value", fieldConfig: { fieldControl: "InputControl" } },
      ],
    },
  ],
}

rs.mock("@/lib/credential-encryption", () => ({
  isTransportEncryptionAvailable: () => encAvailable,
  encryptFormData: (d: Record<string, unknown>) => encryptMock(d),
}))
rs.mock("@/features/workflows/hooks/apis/credentials", () => ({
  useCredentialTypes: () => ({
    data: {
      types: [
        { typeId: "generic_api_key", provider: "GENERIC_HTTP", credentialKind: "API_KEY", displayName: "API Key", formSchema: apiKeySchema, connectAction: "" },
        { typeId: "ms_oauth", provider: "MICROSOFT_TEAMS", credentialKind: "OAUTH2_DELEGATED", displayName: "Teams OAuth", formSchema: null, connectAction: "https://connect/teams" },
      ],
    },
    isLoading: false,
  }),
  useIssueCredential: () => ({ mutateAsync: issueMutate, isPending: false }),
  useTestInlineCredential: () => ({ mutateAsync: testInlineMutate, isPending: false }),
}))
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }))
// The real @ldc/autoform Builder nests values under the schema's root object field
// ("credentialForm") and reports validity; the fill button simulates the user completing
// the Display Name + value fields.
rs.mock("@ldc/autoform", () => ({
  Builder: ({ onValuesChange, onValidChange }: { onValuesChange?: (v: unknown) => void; onValidChange?: (v: boolean) => void }) => (
    <>
      <button
        type="button"
        data-testid="autoform-fill"
        onClick={() => {
          onValuesChange?.({ credentialForm: { displayName: "My Key", value: "k" } })
          onValidChange?.(true)
        }}
      >
        fill
      </button>
      {/* Re-fill with the SAME values (simulates the Builder re-firing onValuesChange on a
          plain re-render — must NOT clear a just-computed test result, SA-1577). */}
      <button
        type="button"
        data-testid="autoform-refill-same"
        onClick={() => onValuesChange?.({ credentialForm: { displayName: "My Key", value: "k" } })}
      >
        refill-same
      </button>
      {/* Edit to DIFFERENT values (a real user edit — must clear a stale test result). */}
      <button
        type="button"
        data-testid="autoform-edit"
        onClick={() => {
          onValuesChange?.({ credentialForm: { displayName: "My Key", value: "k2" } })
          onValidChange?.(true)
        }}
      >
        edit
      </button>
    </>
  ),
}))

import { CredentialAddForm } from "./credential-add-form"

afterEach(() => {
  cleanup(); issueMutate.mockClear(); encryptMock.mockClear(); testInlineMutate.mockClear()
  encAvailable = false; testInlineResult = { valid: true, latencyMs: 42 }
})

describe("CredentialAddForm", () => {
  it("renders the schema, flattens to {displayName, formData}, issues, returns the new id", async () => {
    const onCreated = rs.fn()
    render(<CredentialAddForm provider="GENERIC_HTTP" kinds={["API_KEY"]} onCreated={onCreated} onCancel={() => {}} />)

    pickType("API Key")
    expect(screen.getByTestId("credential-add-form")).toBeTruthy()

    fireEvent.click(screen.getByTestId("autoform-fill")) // fill Display Name + value, mark valid
    fireEvent.click(screen.getByTestId("credential-add-submit"))

    await waitFor(() =>
      expect(issueMutate).toHaveBeenCalledWith({ typeId: "generic_api_key", displayName: "My Key", formData: { value: "k" } }),
    )
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("cred_new"))
  })

  it("encrypts formData to the vault key before issuing when transport crypto is available (FE-2)", async () => {
    encAvailable = true
    render(<CredentialAddForm provider="GENERIC_HTTP" kinds={["API_KEY"]} onCreated={rs.fn()} onCancel={() => {}} />)

    pickType("API Key")
    fireEvent.click(screen.getByTestId("autoform-fill"))
    fireEvent.click(screen.getByTestId("credential-add-submit"))

    await waitFor(() => expect(encryptMock).toHaveBeenCalledWith({ value: "k" }))
    await waitFor(() =>
      expect(issueMutate).toHaveBeenCalledWith({
        typeId: "generic_api_key",
        displayName: "My Key",
        encryptedFormData: { key: "K", iv: "IV", ciphertext: "CT" },
      }),
    )
    // The plaintext secret is never in the request body.
    expect(issueMutate).not.toHaveBeenCalledWith(expect.objectContaining({ formData: expect.anything() }))
  })

  it("submit is disabled until the form reports valid", () => {
    render(<CredentialAddForm provider="GENERIC_HTTP" kinds={["API_KEY"]} onCreated={() => {}} onCancel={() => {}} />)
    pickType("API Key")
    expect((screen.getByTestId("credential-add-submit") as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByTestId("autoform-fill"))
    expect((screen.getByTestId("credential-add-submit") as HTMLButtonElement).disabled).toBe(false)
  })

  it("only lists types matching the node's provider + kinds", () => {
    render(<CredentialAddForm provider="GENERIC_HTTP" kinds={["API_KEY"]} onCreated={() => {}} onCancel={() => {}} />)
    openTypes()
    expect(screen.getByRole("option", { name: "API Key" })).toBeTruthy()
    expect(screen.queryByRole("option", { name: "Teams OAuth" })).toBeNull()
  })

  it("renders a Connect link (no inline form) for OAuth-redirect types", () => {
    render(<CredentialAddForm provider="MICROSOFT_TEAMS" kinds={["OAUTH2_DELEGATED"]} onCreated={() => {}} onCancel={() => {}} />)
    pickType("Teams OAuth")
    expect(screen.getByTestId("credential-add-connect").getAttribute("href")).toBe("https://connect/teams")
    expect(screen.queryByTestId("credential-add-form")).toBeNull()
  })

  // --- SA-1577: Test connection ---
  it("Test button tests the form values inline (no persist) and shows success + latency", async () => {
    render(<CredentialAddForm provider="GENERIC_HTTP" kinds={["API_KEY"]} onCreated={rs.fn()} onCancel={() => {}} />)
    pickType("API Key")
    fireEvent.click(screen.getByTestId("autoform-fill"))
    fireEvent.click(screen.getByTestId("credential-add-test"))
    await waitFor(() =>
      expect(testInlineMutate).toHaveBeenCalledWith({ typeId: "generic_api_key", formData: { value: "k" } }),
    )
    // Test never persists.
    expect(issueMutate).not.toHaveBeenCalled()
    await waitFor(() => {
      const el = screen.getByTestId("credential-add-test-result")
      expect(el.textContent).toContain("nodes.credential.test_ok")
      expect(el.textContent).toContain("42 ms")
    })
  })

  it("Test encrypts the form data when transport crypto is available (no cleartext secret)", async () => {
    encAvailable = true
    render(<CredentialAddForm provider="GENERIC_HTTP" kinds={["API_KEY"]} onCreated={rs.fn()} onCancel={() => {}} />)
    pickType("API Key")
    fireEvent.click(screen.getByTestId("autoform-fill"))
    fireEvent.click(screen.getByTestId("credential-add-test"))
    await waitFor(() =>
      expect(testInlineMutate).toHaveBeenCalledWith({ typeId: "generic_api_key", encryptedFormData: { key: "K", iv: "IV", ciphertext: "CT" } }),
    )
    expect(testInlineMutate).not.toHaveBeenCalledWith(expect.objectContaining({ formData: expect.anything() }))
  })

  it("Test shows the sanitized error when the connection fails", async () => {
    testInlineResult = { valid: false, error: "connection refused" }
    render(<CredentialAddForm provider="GENERIC_HTTP" kinds={["API_KEY"]} onCreated={rs.fn()} onCancel={() => {}} />)
    pickType("API Key")
    fireEvent.click(screen.getByTestId("autoform-fill"))
    fireEvent.click(screen.getByTestId("credential-add-test"))
    await waitFor(() => {
      const el = screen.getByTestId("credential-add-test-result")
      expect(el.textContent).toContain("nodes.credential.test_fail")
      expect(el.textContent).toContain("connection refused")
    })
  })

  it("a stale test result clears when the form values actually change (real edit)", async () => {
    render(<CredentialAddForm provider="GENERIC_HTTP" kinds={["API_KEY"]} onCreated={rs.fn()} onCancel={() => {}} />)
    pickType("API Key")
    fireEvent.click(screen.getByTestId("autoform-fill"))
    fireEvent.click(screen.getByTestId("credential-add-test"))
    await waitFor(() => expect(screen.getByTestId("credential-add-test-result")).toBeTruthy())
    fireEvent.click(screen.getByTestId("autoform-edit")) // value changes k -> k2
    await waitFor(() => expect(screen.queryByTestId("credential-add-test-result")).toBeNull())
  })

  it("keeps the test result on a re-render with unchanged values (SA-1577 guard)", async () => {
    render(<CredentialAddForm provider="GENERIC_HTTP" kinds={["API_KEY"]} onCreated={rs.fn()} onCancel={() => {}} />)
    pickType("API Key")
    fireEvent.click(screen.getByTestId("autoform-fill"))
    fireEvent.click(screen.getByTestId("credential-add-test"))
    await waitFor(() => expect(screen.getByTestId("credential-add-test-result")).toBeTruthy())
    // The Builder re-fires onValuesChange with the SAME values (a plain re-render).
    fireEvent.click(screen.getByTestId("autoform-refill-same"))
    // Result must persist — this is exactly the live bug the guard fixes.
    await new Promise(r => setTimeout(r, 50))
    expect(screen.getByTestId("credential-add-test-result")).toBeTruthy()
  })

  it("Test button is disabled until the form reports valid", () => {
    render(<CredentialAddForm provider="GENERIC_HTTP" kinds={["API_KEY"]} onCreated={() => {}} onCancel={() => {}} />)
    pickType("API Key")
    expect((screen.getByTestId("credential-add-test") as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByTestId("autoform-fill"))
    expect((screen.getByTestId("credential-add-test") as HTMLButtonElement).disabled).toBe(false)
  })
})
