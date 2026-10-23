// Client-side filtering for the node credential picker. credentials-management's
// GET /credentials and GET /credential-types take no provider/kind query params
// (verified against credentials_controller.py), so the picker filters the full
// tenant list down to the node's declared provider + supported kinds.
import type { ICredentialFilter } from "@/features/workflows/types/credentials"

export const filterCredentials = <T extends { provider: string; credentialKind: string }>(
  items: T[],
  { provider, kinds }: ICredentialFilter,
): T[] =>
  items.filter(
    item =>
      (!provider || item.provider === provider) &&
      (!kinds || kinds.length === 0 || kinds.includes(item.credentialKind)),
  )

export const findCredential = <T extends { id: string }>(items: T[], id: string): T | undefined =>
  id ? items.find(item => item.id === id) : undefined
