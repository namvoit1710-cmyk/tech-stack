// REST client for the credentials-management vault (T7, owner decision D2).
// Separate @ldc/api-sdk instance so a node's credential_ref is a credentials-management
// id that Part B's VaultCredentialClient resolves at run time. Dormant (null) when the
// base URL is unset → the picker is never injected (D3, non-breaking).
import { env } from "@/env";
import { APISdk } from "@ldc/api-sdk";

const baseUrl = env.PUBLIC_CREDENTIALS_MANAGEMENT_API_URL;

export const isCredentialsConfigured = (): boolean => Boolean(baseUrl);

// credentials-management scopes /credentials* by x-tenant-id (required) and x-user-id
// (the list is per-user), normally injected by the API gateway from the JWT. In local dev
// (no gateway) we pass the configured dev tenant/user; unset → that header is omitted.
const devTenant = env.PUBLIC_DEV_TENANT_ID;
const devUser = env.PUBLIC_DEV_USER_ID;
const tenantHeaders =
  devTenant || devUser
    ? { ...(devTenant ? { "x-tenant-id": devTenant } : {}), ...(devUser ? { "x-user-id": devUser } : {}) }
    : undefined;

export const credentialsApi = baseUrl
  ? new APISdk({
      baseURL: baseUrl + "/api/v1",
      timeout: 5 * 60 * 1000,
      withCredentials: false,
      headers: tenantHeaders,
      onSessionExpired: () => {
        window.location.href = "/login";
      },
    })
  : null;

// Non-null accessor for hooks. Callers are gated (queries by `enabled`, the Add form by the
// dormant field-injection), so this never throws in practice — it just removes the `!` and
// fails loudly if a future caller forgets the gate.
export const requireCredentialsApi = (): APISdk => {
  if (!credentialsApi) {
    throw new Error("credentials-management API is not configured (PUBLIC_CREDENTIALS_MANAGEMENT_API_URL unset)");
  }
  return credentialsApi;
};
