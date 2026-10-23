import { z } from "zod";

import { createClientEnv } from "@ldc/env";

const env = createClientEnv(
  {
    PUBLIC_URL_GOVERNANCE_API: z.url(),
    PUBLIC_URL_FILE_SERVICE: z.url(),
    PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE: z.url(),
    PUBLIC_WORKFLOW_VALIDATION_RULE_AGENT_API_URL: z.url(),
    PUBLIC_WORKFLOW_GOVERNANCE_AGENT_API_URL: z.url(),
    PUBLIC_WORKFLOW_PROFILE_MANAGER_API_URL: z.url(),
    PUBLIC_WORKFLOW_SUPERVISOR_AGENT_API_URL: z.url(),
    // Direct base URL for the Workflow Designer Agent (SA-1437). Optional → when unset the
    // "Create with AI" chat falls back to the supervisor URL, so this stays non-breaking
    // until the deployment env provisions it.
    PUBLIC_WORKFLOW_DESIGNER_AGENT_API_URL: z.url().optional(),
    PUBLIC_INTEGRATION_HUB_API_URL: z.url(),
    PUBLIC_URL_PUSH_GATEWAY: z.url(),
    // T7: credentials-management vault base URL. Optional → when unset the node
    // credential picker stays dormant (non-breaking), mirroring the backend's
    // CREDENTIALS_VAULT_URL flag.
    PUBLIC_CREDENTIALS_MANAGEMENT_API_URL: z.url().optional(),
    // Dev-only tenant for direct vault calls. In prod the API gateway injects x-tenant-id
    // from the verified JWT; in local dev (no gateway) the caller passes it. Optional →
    // no tenant header is sent when unset, so nothing tenant-specific ships in code.
    PUBLIC_DEV_TENANT_ID: z.string().optional(),
    PUBLIC_DEV_USER_ID: z.string().optional(),
    // SA-2532 — auth source. "bff" makes this app log in standalone against the
    // control-plane Keycloak BFF (/api/v1/auth/*). Unset keeps the legacy
    // behaviour where the shell supplies the session via mf-auth.
    PUBLIC_AUTH_MODE: z.enum(["mf", "bff"]).optional(),
  },
  {

    PUBLIC_URL_GOVERNANCE_API: import.meta.env.PUBLIC_URL_GOVERNANCE_API,
    PUBLIC_URL_FILE_SERVICE: import.meta.env.PUBLIC_URL_FILE_SERVICE,
    PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE: import.meta.env.PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE,
    PUBLIC_WORKFLOW_VALIDATION_RULE_AGENT_API_URL: import.meta.env.PUBLIC_WORKFLOW_VALIDATION_RULE_AGENT_API_URL,
    PUBLIC_WORKFLOW_GOVERNANCE_AGENT_API_URL: import.meta.env.PUBLIC_WORKFLOW_GOVERNANCE_AGENT_API_URL,
    PUBLIC_WORKFLOW_PROFILE_MANAGER_API_URL: import.meta.env.PUBLIC_WORKFLOW_PROFILE_MANAGER_API_URL,
    PUBLIC_WORKFLOW_SUPERVISOR_AGENT_API_URL: import.meta.env.PUBLIC_WORKFLOW_SUPERVISOR_AGENT_API_URL,
    // Dormant tiers arrive as "" (not undefined) from the pipeline, so normalize to
    // undefined to keep z.url().optional() accepting the unset state (mirrors below).
    PUBLIC_WORKFLOW_DESIGNER_AGENT_API_URL: import.meta.env.PUBLIC_WORKFLOW_DESIGNER_AGENT_API_URL || undefined,
    PUBLIC_INTEGRATION_HUB_API_URL: import.meta.env.PUBLIC_INTEGRATION_HUB_API_URL,
    PUBLIC_URL_PUSH_GATEWAY: import.meta.env.PUBLIC_URL_PUSH_GATEWAY,
    // The pipeline materializes every frontendEnv key into .env.production, so a
    // dormant tier arrives as "" (not undefined) — normalize so z.url().optional()
    // keeps accepting the dormant state.
    PUBLIC_CREDENTIALS_MANAGEMENT_API_URL: import.meta.env.PUBLIC_CREDENTIALS_MANAGEMENT_API_URL || undefined,
    PUBLIC_DEV_TENANT_ID: import.meta.env.PUBLIC_DEV_TENANT_ID || undefined,
    PUBLIC_DEV_USER_ID: import.meta.env.PUBLIC_DEV_USER_ID || undefined,
    // SA-2532 — default to the standalone Keycloak-BFF mode. The pipeline can
    // still override with PUBLIC_AUTH_MODE=mf when this app is embedded in the
    // shell (which supplies the session via mf-auth).
    PUBLIC_AUTH_MODE: import.meta.env.PUBLIC_AUTH_MODE || "bff",
  },
);

export { env };

