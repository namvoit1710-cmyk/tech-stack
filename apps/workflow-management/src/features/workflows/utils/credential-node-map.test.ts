import { describe, expect, it } from "@rstest/core"
import { resolveCredentialAffinity } from "./credential-node-map"

// Minimal node-detail stub; resolveCredentialAffinity only reads these fields.
const node = (over: Record<string, unknown>) =>
  ({
    id: "x",
    worker_type: null,
    base_worker_id: null,
    base_node_type: null,
    supported_credential_kinds: [],
    credential_provider: null,
    ...over,
  }) as never

describe("resolveCredentialAffinity", () => {
  it("uses catalog-declared kinds + provider when the control plane seeds them", () => {
    expect(
      resolveCredentialAffinity(
        node({ worker_type: "http_request", supported_credential_kinds: ["api_key", "bearer"], credential_provider: "GITHUB" }),
      ),
    ).toEqual({ provider: "GITHUB", kinds: ["api_key", "bearer"] })
  })

  it("falls back to show-all for a known credential-bearing worker_type when kinds are empty", () => {
    // http_request is credential-bearing but the live catalog hasn't seeded kinds yet.
    expect(resolveCredentialAffinity(node({ worker_type: "http_request" }))).toEqual({ kinds: [] })
    expect(resolveCredentialAffinity(node({ worker_type: "ms_teams" }))).toEqual({ kinds: [] })
    expect(resolveCredentialAffinity(node({ worker_type: "email" }))).toEqual({ kinds: [] })
  })

  it("uses the catalog-seeded kinds for the email worker (vault-only auth)", () => {
    expect(
      resolveCredentialAffinity(
        node({ worker_type: "email", supported_credential_kinds: ["BASIC_AUTH", "OAUTH2_APPLICATION"] }),
      ),
    ).toEqual({ provider: undefined, kinds: ["BASIC_AUTH", "OAUTH2_APPLICATION"] })
  })

  it("resolves the worker key from base_worker_id for business nodes derived from a worker", () => {
    expect(resolveCredentialAffinity(node({ worker_type: null, base_worker_id: "worker:ms_teams" }))).toEqual({ kinds: [] })
  })

  it("returns null for non-credential-bearing nodes (builtins, code worker)", () => {
    expect(resolveCredentialAffinity(node({ id: "INPUT" }))).toBeNull()
    expect(resolveCredentialAffinity(node({ worker_type: "code" }))).toBeNull()
  })
})
