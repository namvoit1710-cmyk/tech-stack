# frontend-workflow-management — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. This is a static SPA bundle;
build-time env is baked into the bundle at docker build. Keep current in the same PR.

## Build-time env (baked into the bundle at build — must be correct for the target env)
`frontendEnv` is materialized into `<buildContext>/.env.production` BEFORE `docker build`, so rsbuild
bakes these URLs into the bundle. Each must exist and be correct for the target tier at BUILD time:

- `PUBLIC_URL_FILE_SERVICE` -> file-service route.
- `PUBLIC_URL_ORCHESTRATOR` -> ai-workflow-orchestrators route.
- `PUBLIC_URL_API_GATEWAY` -> api-gateway route (dev only).
- `PUBLIC_URL_PUSH_GATEWAY` -> push-gateway route.
- `PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE` -> ai-workflow control-plane route.
- `PUBLIC_URL_GOVERNANCE_API` -> governance API route.
- `PUBLIC_WORKFLOW_VALIDATION_RULE_AGENT_API_URL` -> validation-rule-agent route.
- `PUBLIC_WORKFLOW_GOVERNANCE_AGENT_API_URL` -> governance-agent route.
- `PUBLIC_WORKFLOW_PROFILE_MANAGER_API_URL` -> profile-agent route.
- `PUBLIC_INTEGRATION_HUB_API_URL` -> integration-hub backend route.
- `PUBLIC_URL_SCHEMA_TRANSFORM` -> schema-transform route (dev only).
- `PUBLIC_URL_API_GATEWAY_PROFILE_MANAGEMENT` -> profile-management gateway route (dev only).
- `PUBLIC_URL_API_GATEWAY_FEATURE_MANAGEMENT` -> feature-management gateway route (dev only).
- `PUBLIC_WORKFLOW_SUPERVISOR_AGENT_API_URL` -> workflow-supervisor-agent route (dev only).
- `PUBLIC_WORKFLOW_DESIGNER_AGENT_API_URL` -> workflow-designer-agent route ("Create with AI" chat;
  dev template, `tenantize.sh` swaps `smdg-ai-dev-` -> `smdg-ai-tenant-1-`). If unset the FE falls
  back to the supervisor URL.
- `PUBLIC_URL_PROFILE_MANAGEMENT` -> profile-management route (dev only).
- `PUBLIC_CREDENTIALS_MANAGEMENT_API_URL` -> credentials vault. **Dormant** on dev/qas (empty =
  credential picker + settings page stay hidden); activated for **tenant-1** only. If set, the vault
  service must be deployed for that tier.
- `PUBLIC_DEV_TENANT_ID` / `PUBLIC_DEV_USER_ID` -> tenant-1 only (`system`); the FE sends
  `x-tenant-id`/`x-user-id` itself where there is no gateway in front of the vault. Empty elsewhere.
- `PUBLIC_APP_VERSION` -> `1.0.0` (dev).

> Note: several **qas** values resolve to `…agent-orchestrator-qas…` rather than the named service.
> Confirm each qas route points at the intended backend before promoting to qas.

## Upstream services / remotes (must be deployed & reachable)
- All backends above must be deployed and reachable in the target env for the matching UI features.
- On **tenant-1**, the credentials-management (vault) service must be deployed for the credential
  picker/settings to appear.

## Verify after deploy
- Page loads at the workflow-management route.
- Workflow features reach their baked routes (orchestrator, control-plane, governance/validation/
  designer agents, IH, schema-transform) and return; "Create with AI" chat reaches the designer agent.
