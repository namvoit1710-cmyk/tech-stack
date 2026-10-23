import { describe, expect, it } from "@rstest/core"
import type { ICredentialSummary } from "@/features/workflows/types/credentials"
import { filterCredentials, findCredential } from "./credential-filter"

const cred = (provider: string, credentialKind: string): ICredentialSummary => ({
  id: `${provider}:${credentialKind}`,
  typeId: "t",
  displayName: `${provider} ${credentialKind}`,
  provider,
  credentialKind,
  status: "ACTIVE",
})

const items = [
  cred("GENERIC_HTTP", "API_KEY"),
  cred("GENERIC_HTTP", "BASIC_AUTH"),
  cred("MICROSOFT_TEAMS", "webhook_url"),
]

describe("filterCredentials", () => {
  it("filters by provider and credentialKind", () => {
    expect(filterCredentials(items, { provider: "GENERIC_HTTP", kinds: ["API_KEY"] }).map(i => i.id))
      .toEqual(["GENERIC_HTTP:API_KEY"])
  })

  it("returns everything when the filter is empty", () => {
    expect(filterCredentials(items, {})).toHaveLength(3)
  })

  it("filters by provider only when kinds is empty", () => {
    expect(filterCredentials(items, { provider: "GENERIC_HTTP", kinds: [] })).toHaveLength(2)
  })

  it("excludes credentials whose kind is not in the list", () => {
    expect(filterCredentials(items, { kinds: ["webhook_url"] }).map(i => i.provider))
      .toEqual(["MICROSOFT_TEAMS"])
  })
})

describe("findCredential", () => {
  it("returns the matching credential by id", () => {
    expect(findCredential(items, "GENERIC_HTTP:API_KEY")?.credentialKind).toBe("API_KEY")
  })
  it("returns undefined for an unknown id", () => {
    expect(findCredential(items, "missing")).toBeUndefined()
  })
  it("returns undefined for an empty id", () => {
    expect(findCredential(items, "")).toBeUndefined()
  })
})
