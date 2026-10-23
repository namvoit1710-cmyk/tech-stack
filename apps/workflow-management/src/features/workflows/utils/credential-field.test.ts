import { describe, expect, it } from "@rstest/core"
import { buildCredentialField } from "./credential-field"

// resolveCredentialAffinity reads these fields off the node detail.
const node = (over: Record<string, unknown> = {}) =>
  ({
    id: "x",
    worker_type: null,
    base_worker_id: null,
    base_node_type: null,
    supported_credential_kinds: [],
    credential_provider: null,
    ...over,
  }) as never

describe("buildCredentialField", () => {
  it("builds a credential_ref picker field from catalog-declared kinds + provider", () => {
    const f = buildCredentialField(
      node({ worker_type: "http_request", supported_credential_kinds: ["API_KEY"], credential_provider: "GENERIC_HTTP" }),
      "Credential",
    )!
    expect(f.key).toBe("credential_ref")
    expect(f.outputType).toBe("string")
    expect(f.fieldConfig.fieldControl).toBe("CredentialPickerControl")
    expect(f.fieldConfig.controlProps).toMatchObject({ provider: "GENERIC_HTTP", kinds: ["API_KEY"] })
    expect(f.fieldConfig.wrapperProps?.label).toBe("Credential")
  })

  it("builds a field (show-all) for a credential-bearing worker even when catalog kinds are empty", () => {
    const f = buildCredentialField(node({ worker_type: "http_request" }), "Credential")!
    expect(f.key).toBe("credential_ref")
    expect(f.fieldConfig.controlProps).toMatchObject({ kinds: [] })
  })

  it("returns null for non-credential-bearing nodes", () => {
    expect(buildCredentialField(node({ id: "INPUT" }), "Credential")).toBeNull()
    expect(buildCredentialField(node({ worker_type: "code" }), "Credential")).toBeNull()
  })
})
