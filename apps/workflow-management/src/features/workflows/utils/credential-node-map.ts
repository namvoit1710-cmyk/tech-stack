// Resolves a node's credential affinity (which credentials it can use) for the
// node-config credential picker (T7.2). The control plane catalog is authoritative:
// when it seeds `supported_credential_kinds`, we use those (+ credential_provider).
//
// ponytail: until the catalog seeds kinds for the credential-bearing workers, a static
// allow-list of worker_types keeps the picker visible (show-all, kinds:[]). Delete the
// fallback set once `supported_credential_kinds` is populated for these workers.
import type { INodeCatalogDetail } from "@/features/workflows/types/node-pallete"

export interface ICredentialAffinity {
  provider?: string
  kinds: string[]
}

type CredentialNodeMeta = Pick<
  INodeCatalogDetail,
  "supported_credential_kinds" | "credential_provider" | "worker_type" | "base_worker_id" | "base_node_type" | "id"
>

// worker_type values whose nodes consume a credential (verified against the live
// node catalog + the owner's CredentialKind→worker table). `code` is excluded.
const CREDENTIAL_BEARING_WORKERS = new Set([
  "http_request",
  "ms_teams",
  "database",
  "database_connection",
  "agent",
  "agent_worker",
  "email",
])

const stripPrefix = (s?: string | null): string | undefined => (s ? (s.split(":").pop() ?? s) : undefined)

export const resolveCredentialAffinity = (node: CredentialNodeMeta): ICredentialAffinity | null => {
  const catalogKinds = node.supported_credential_kinds ?? []
  if (catalogKinds.length > 0) {
    return { provider: node.credential_provider ?? undefined, kinds: catalogKinds }
  }

  const workerKey =
    node.worker_type ?? stripPrefix(node.base_worker_id) ?? node.base_node_type ?? stripPrefix(node.id)
  if (workerKey && CREDENTIAL_BEARING_WORKERS.has(workerKey)) {
    return { kinds: [] }
  }
  return null
}
