import { env } from "@/env";
import { APISdk } from "@ldc/api-sdk";

// SA-2532 — in standalone Keycloak-BFF mode the app sends the HttpOnly session
// cookie with every request (withCredentials) and, on 401, bounces to the BFF
// login (which redirects to Keycloak → Azure). In the legacy shell/mf-auth mode
// nothing changes: no cookie, and expiry sends the user to the shell's /login.
const BFF_MODE = env.PUBLIC_AUTH_MODE === "bff";

const onSessionExpired = () => {
    if (BFF_MODE) {
        const redirect = encodeURIComponent(window.location.href);
        window.location.href = `${env.PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE}/api/v1/auth/login?redirect=${redirect}`;
    } else {
        window.location.href = "/login";
    }
};

const api = new APISdk({
    baseURL: env.PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE + "/api/v1",
    timeout: 5 * 60 * 1000,
    withCredentials: BFF_MODE,
    onSessionExpired,
});

const fileApi = new APISdk({
    baseURL: env.PUBLIC_URL_FILE_SERVICE + "/api/v1",
    timeout: 5 * 60 * 1000,
    withCredentials: BFF_MODE,
    onSessionExpired,
});

const apiV2 = new APISdk({
    baseURL: env.PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE + "/api/v2",
    timeout: 5 * 60 * 1000,
    withCredentials: BFF_MODE,
    onSessionExpired,
});

const workflowValidationRuleAgentApi = new APISdk({
    baseURL: env.PUBLIC_WORKFLOW_VALIDATION_RULE_AGENT_API_URL,
    timeout: 5 * 60 * 1000,
    withCredentials: BFF_MODE,
});

const workflowProfileManagerApi = new APISdk({
    baseURL: env.PUBLIC_WORKFLOW_PROFILE_MANAGER_API_URL,
    timeout: 5 * 60 * 1000,
    withCredentials: BFF_MODE,
});

const workflowAgentApi = new APISdk({
    baseURL: env.PUBLIC_WORKFLOW_SUPERVISOR_AGENT_API_URL,
    timeout: 5 * 60 * 1000,
    withCredentials: BFF_MODE,
});

// SA-1582: the "Create with AI" chat talks to the Workflow Designer Agent (SA-1437)
// directly. Falls back to the supervisor URL when the designer URL is unset so the
// feature stays working until the deployment env provisions the dedicated tier.
const workflowDesignerAgentApi = new APISdk({
    baseURL:
        env.PUBLIC_WORKFLOW_DESIGNER_AGENT_API_URL ??
        env.PUBLIC_WORKFLOW_SUPERVISOR_AGENT_API_URL,
    timeout: 5 * 60 * 1000,
    withCredentials: BFF_MODE,
});

export { api, apiV2, fileApi, workflowAgentApi, workflowDesignerAgentApi, workflowProfileManagerApi, workflowValidationRuleAgentApi };

export default api;
