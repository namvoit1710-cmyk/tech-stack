// Builds the synthetic `credential_ref` picker field the node-config drawer prepends
// to a credential-bearing node's parameters (T7). Pure: returns null unless the node
// is credential-bearing (per the catalog's supported_credential_kinds, or the
// worker_type fallback in credential-node-map). The picker itself degrades gracefully
// when the vault is unconfigured, so injection no longer gates on configured state.
import { resolveCredentialAffinity } from "@/features/workflows/utils/credential-node-map"
import type { INodeCatalogDetail } from "@/features/workflows/types/node-pallete"
import type { IField } from "@ldc/autoform"

type CredentialNodeMeta = Pick<
  INodeCatalogDetail,
  "supported_credential_kinds" | "credential_provider" | "worker_type" | "base_worker_id" | "base_node_type" | "id"
>

export const buildCredentialField = (node: CredentialNodeMeta, label?: string): IField | null => {
  const affinity = resolveCredentialAffinity(node)
  if (!affinity) return null
  return {
    key: "credential_ref",
    outputType: "string",
    fieldConfig: {
      fieldWrapper: "FormItemWrapper",
      wrapperProps: {
        label,
        labelSpan: "xl-12 lg-12 md-12 xs-12",
        fieldSpan: "xl-12 lg-12 md-12 xs-12",
      },
      fieldControl: "CredentialPickerControl",
      controlProps: {
        provider: affinity.provider,
        kinds: affinity.kinds,
        className: "w-full",
      },
    },
  }
}
