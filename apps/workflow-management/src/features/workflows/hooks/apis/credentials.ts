// React-query hooks for the credentials-management vault (T7). Thin wrappers over the
// dormant-aware credentialsApi; queries are disabled until the vault is configured.
// baseURL already includes /api/v1, so paths are root-relative here.
//
// Every credentials-management response is wrapped in a {status, data} envelope (same as
// the workflow control plane), so each fn unwraps `.data` — the hooks expose the inner
// payload to callers. Verified live against :8007 (the envelope is what an early build missed).
import { isCredentialsConfigured, requireCredentialsApi } from "@/lib/credentials-api";
import { queryKeyFactory, useMutation, useQuery } from "@ldc/tanstack-query";
import type {
  ICredentialFormValues,
  IIssueCredentialRequest,
  IIssueCredentialResponse,
  IListCredentialsResponse,
  IListCredentialTypesResponse,
  IRotateCredentialKeyResponse,
  ITestCredentialResult,
  ITestInlineCredentialRequest,
  IUpdateCredentialRequest,
  IUpdateCredentialResponse,
} from "../../types/credentials";

interface IVaultEnvelope<T> {
  status: string;
  data: T;
}

const _credentialKeys = queryKeyFactory("credentials");

export const credentialKeys = {
  ..._credentialKeys,
  listTypes: () => [..._credentialKeys.lists(), "credential-types"] as const,
  list: () => [..._credentialKeys.lists(), "credentials"] as const,
  formValues: (id: string) => [..._credentialKeys.details(), id, "form-values"] as const,
};

export const useCredentialTypes = () =>
  useQuery({
    queryKey: credentialKeys.listTypes(),
    enabled: isCredentialsConfigured(),
    queryFn: () =>
      requireCredentialsApi()
        .get<IVaultEnvelope<IListCredentialTypesResponse>>("/credential-types")
        .then(r => r.data),
  });

export const useCredentials = () =>
  useQuery({
    queryKey: credentialKeys.list(),
    enabled: isCredentialsConfigured(),
    queryFn: () =>
      requireCredentialsApi()
        .get<IVaultEnvelope<IListCredentialsResponse>>("/credentials")
        .then(r => r.data),
  });

// Non-secret stored form values for one credential, to prefill the edit form.
// Secrets are excluded by the vault (per-adapter allowlist); only fetched when an id
// is supplied and the vault is configured.
export const useCredentialFormValues = (id: string) =>
  useQuery({
    queryKey: credentialKeys.formValues(id),
    enabled: isCredentialsConfigured() && Boolean(id),
    queryFn: () =>
      requireCredentialsApi()
        .get<IVaultEnvelope<ICredentialFormValues>>(`/credentials/${id}`)
        .then(r => r.data),
  });

export const useIssueCredential = () =>
  useMutation({
    mutationFn: (body: IIssueCredentialRequest) =>
      requireCredentialsApi()
        .post<IVaultEnvelope<IIssueCredentialResponse>>("/credentials", body)
        .then(r => r.data),
  });

export const useTestCredential = () =>
  useMutation({
    mutationFn: (credentialId: string) =>
      requireCredentialsApi()
        .get<IVaultEnvelope<ITestCredentialResult>>(`/credentials/${credentialId}/test`)
        .then(r => r.data),
  });

// SA-1577: test form values before saving (Add-credential flow). Runs the adapter's live
// test server-side (SELECT 1 / SMTP handshake / Graph token / webhook probe) and returns
// {valid, error, latencyMs} — never persists a credential.
export const useTestInlineCredential = () =>
  useMutation({
    mutationFn: (body: ITestInlineCredentialRequest) =>
      requireCredentialsApi()
        .post<IVaultEnvelope<ITestCredentialResult>>("/credentials/test-inline", body)
        .then(r => r.data),
  });

// PATCH rename and/or replace the secret. `version` (from the row) is sent as
// If-Match so a stale edit fails with 409 (optimistic concurrency, BE-4).
export const useUpdateCredential = () =>
  useMutation({
    mutationFn: ({
      credentialId,
      body,
      version,
    }: {
      credentialId: string;
      body: IUpdateCredentialRequest;
      version?: number;
    }) =>
      requireCredentialsApi()
        .patch<IVaultEnvelope<IUpdateCredentialResponse>>(
          `/credentials/${credentialId}`,
          body,
          version !== undefined ? { headers: { "If-Match": String(version) } } : undefined,
        )
        .then(r => r.data),
  });

export const useDeleteCredential = () =>
  useMutation({
    mutationFn: (credentialId: string) =>
      requireCredentialsApi().delete(`/credentials/${credentialId}`),
  });

// Re-wrap the stored secret under fresh key material (BE-3 rotate-key). The
// secret itself is unchanged; only the ciphertext/DEK rotate.
export const useRotateCredentialKey = () =>
  useMutation({
    mutationFn: (credentialId: string) =>
      requireCredentialsApi()
        .post<IVaultEnvelope<IRotateCredentialKeyResponse>>(`/credentials/${credentialId}/rotate-key`)
        .then(r => r.data),
  });
