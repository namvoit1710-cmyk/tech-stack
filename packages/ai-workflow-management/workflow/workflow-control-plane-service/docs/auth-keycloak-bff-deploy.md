# Keycloak BFF — config & deployment (SA-2534 / SA-2535)

How to turn the Keycloak BFF on for the **tenant-1** standalone workflow-management
deployment. The code ships **off by default** (`AUTH_ENABLED` unset) — enabling is pure
configuration.

## 1. Secrets — store in the vault, never in source (SA-2534)

Two secrets must come from the platform credential store / bound service, not from the
repo or a plain manifest:

| Secret | What it is |
|---|---|
| `KEYCLOAK_CLIENT_SECRET` | secret of the confidential Keycloak client `gateway-bff-fm` (realm tenant-1) |
| `AUTH_SESSION_SECRET` | random string that signs/encrypts the session + state cookies (Fernet) |

On SAP BTP Cloud Foundry, bind them via a user-provided service / credential store and
surface them as env vars — do **not** commit them.

## 2. Control-plane (BFF) env — `workflow-control-plane-service`

```
AUTH_ENABLED=true
KEYCLOAK_ISSUER=https://136-85-27-214.sslip.io/realms/tenant-1
KEYCLOAK_CLIENT_ID=gateway-bff-fm
KEYCLOAK_CLIENT_SECRET=<vault>
KEYCLOAK_REDIRECT_URI=https://<control-plane-host>/api/v1/auth/callback
KEYCLOAK_SCOPE=openid profile email
AUTH_SESSION_SECRET=<vault>
AUTH_POST_LOGIN_REDIRECT=https://<frontend-host>/
AUTH_POST_LOGOUT_REDIRECT=https://<frontend-host>/
AUTH_JWT_AUDIENCE=smdg-ai-api
# Cross-origin (SPA host != control-plane host):
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=none
CORS_ORIGINS=https://<frontend-host>
```

## 3. Frontend env — `workflow-management`

```
PUBLIC_AUTH_MODE=bff
PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE=https://<control-plane-host>
```

## 4. Keycloak client — register the deployed URLs

On the `gateway-bff-fm` client (realm tenant-1), add:
- Redirect URI: `https://<control-plane-host>/api/v1/auth/callback`
- Web origin: `https://<frontend-host>`
- Post-logout redirect: `https://<frontend-host>/*`

The Azure app registration already carries the realm broker redirect
(`.../realms/tenant-1/broker/{entra-oidc,microsoft}/endpoint`).

## 5. Deploy & smoke

1. `cf push` the control-plane and the frontend with the env above (or run the pipeline).
2. Smoke: open the frontend → it should redirect to Keycloak → Azure → back, then
   `GET /api/v1/auth/me` returns the user and an authenticated API call succeeds.
3. Re-run the use cases attached to SA-2528 (UC-1 … UC-8).

## Notes / follow-ups

- Session store is in-memory (single instance). For multi-pod, back `SessionStore`
  with Redis (`REDIS_URL` is already available).
- First cut protects the control-plane + its APIs; other backends (file-service,
  agents) adopt Bearer JWT verification (SA-2533, `require_jwt`).
