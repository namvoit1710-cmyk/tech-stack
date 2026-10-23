// Wire DTOs for the credentials-management vault (camelCase, verified against
// credentials-management/.../dtos/credential_dtos.py). The node credential picker (T7)
// stores only a credential id (`credential_ref`) on a node — never a secret.

import type { ISchema } from "@ldc/autoform"

// An @ldc/autoform ISchema document (or null for OAuth-redirect types → Connect button).
export type ICredentialFormSchema = ISchema

export interface ICredentialSummary {
  id: string
  typeId: string
  displayName: string
  provider: string
  credentialKind: string
  status: string // ACTIVE | EXPIRING_SOON | EXPIRED | REVOKED (backend-derived)
  scopes?: string[]
  version?: number // optimistic-concurrency token (sent back as If-Match on update)
  expiresAt?: string | null
  createdAt?: string | null
  updatedAt?: string | null
  // SA-2279. Whether scheduled / event-triggered runs may use this credential.
  // Event triggers have no user behind them, so the vault's owner check refuses
  // them unless the owner has granted this explicitly.
  automationAllowed?: boolean
}

export interface ICredentialTypeDefinition {
  typeId: string
  provider: string
  credentialKind: string
  displayName: string
  description?: string
  iconUrl?: string
  connectAction?: string
  formSchema: ICredentialFormSchema | null
  supportedScopes?: string[]
}

export interface IListCredentialsResponse {
  credentials: ICredentialSummary[]
}

export interface IListCredentialTypesResponse {
  types: ICredentialTypeDefinition[]
}

// Hybrid transport envelope (RSA-OAEP-wrapped AES-GCM), all base64 — see
// lib/credential-encryption.ts. Mirrors the vault's EncryptedEnvelope DTO.
export interface IEncryptedEnvelope {
  key: string
  iv: string
  ciphertext: string
}

export interface IIssueCredentialRequest {
  typeId: string
  displayName: string
  // Exactly one of these: plaintext formData, or the encrypted envelope (FE-2).
  formData?: Record<string, unknown>
  encryptedFormData?: IEncryptedEnvelope
}

// SA-1577: test a credential's form values WITHOUT persisting (Add-credential flow).
// Same secret-transport rules as issue: exactly one of formData / encryptedFormData.
export interface ITestInlineCredentialRequest {
  typeId: string
  formData?: Record<string, unknown>
  encryptedFormData?: IEncryptedEnvelope
}

export interface IIssueCredentialResponse {
  credentialId: string
  redirectUrl?: string | null
  provider?: string
  credentialKind?: string
  status?: string
  expiresAt?: string | null
  version?: number
}

// Non-secret stored form values for prefilling the edit form (secrets excluded by
// the vault's per-adapter allowlist). Mirrors CredentialFormValuesResponse.
export interface ICredentialFormValues {
  id: string
  typeId: string
  displayName: string
  provider: string
  credentialKind: string
  version: number
  values: Record<string, unknown>
  expiresAt?: string | null
}

export interface IUpdateCredentialRequest {
  displayName?: string
  formData?: Record<string, unknown>
  // Omit to leave the grant untouched. Sending `false` WITHDRAWS it, so a
  // partial edit must not default this to false.
  automationAllowed?: boolean
}

export interface IUpdateCredentialResponse {
  credentialId: string
  version: number
  status?: string
  expiresAt?: string | null
  automationAllowed?: boolean
}

export interface IRotateCredentialKeyResponse {
  credentialId: string
  version: number
  rotatedAt?: string | null
}

export interface ITestCredentialResult {
  valid: boolean
  error?: string | null
  latencyMs?: number
}

// Node-declared affinity (Part B T6 exposes these on the catalog DTO).
export interface ICredentialFilter {
  provider?: string
  kinds?: string[]
}
