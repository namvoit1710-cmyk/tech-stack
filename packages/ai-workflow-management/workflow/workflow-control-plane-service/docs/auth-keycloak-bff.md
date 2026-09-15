# Keycloak BFF (SA-2530)

The control plane can act as a **Backend-For-Frontend**: it runs the OIDC
Authorization Code + PKCE flow against Keycloak (Azure-federated) and hands the
browser an **opaque HttpOnly session cookie**. The SPA never holds a token — the
OIDC tokens live server-side in the session store. An XSS on the SPA therefore
cannot lift an access token.

Gated behind `AUTH_ENABLED` (default **off**). When off, the service behaves
exactly as before (identity via the legacy `X-User` header) and every `/auth/*`
route returns `404`.

## Endpoints (mounted at `/api/v1/auth`)

| Route | Purpose |
|---|---|
| `GET /login?redirect=<url>` | 307 → Keycloak authorize (state + PKCE stored in a short-lived encrypted cookie). Keycloak brokers to Azure. `redirect` is where the browser lands after callback. |
| `GET /callback?code=&state=` | Exchange code for tokens, read `/userinfo`, create the server-side session, set the `wf_session` cookie, 307 back to the SPA. |
| `GET /me` | The current user `{id,email,name,roles}` (raw JSON), or `401`. |
| `POST /logout` | Drop the session + cookie; returns `{ "logout_url": ... }` (Keycloak end-session) for the SPA to navigate to. |

## Flow

```
SPA (not authed) → GET /api/v1/auth/login
  → 307 Keycloak /authorize → Azure AD → Keycloak → 307 GET /api/v1/auth/callback
  → BFF exchanges code (client secret + PKCE), reads /userinfo,
    stores tokens server-side, sets HttpOnly wf_session cookie
  → 307 back to the SPA. SPA now calls the API with the cookie (withCredentials:true).
```

## Configuration (env)

| Var | Notes |
|---|---|
| `AUTH_ENABLED` | `true` to turn the BFF on. |
| `KEYCLOAK_ISSUER` | e.g. `https://136-85-27-214.sslip.io/realms/tenant-1`. |
| `KEYCLOAK_CLIENT_ID` | Confidential client (default `gateway-bff-fm`). |
| `KEYCLOAK_CLIENT_SECRET` | Client secret (store in the vault, not source). |
| `KEYCLOAK_REDIRECT_URI` | `<public-bff-url>/api/v1/auth/callback` — must be registered on the Keycloak client. |
| `KEYCLOAK_SCOPE` | Default `openid profile email`. |
| `AUTH_POST_LOGIN_REDIRECT` / `AUTH_POST_LOGOUT_REDIRECT` | SPA URLs to return to. |
| `AUTH_SESSION_SECRET` | Signs/encrypts the cookies (Fernet). **Required** when enabled. |
| `AUTH_SESSION_COOKIE` | Cookie name (default `wf_session`). |
| `AUTH_SESSION_TTL_SECONDS` | Session lifetime (default `3600`). |
| `AUTH_COOKIE_SECURE` | Default `true`. |
| `AUTH_COOKIE_SAMESITE` | `lax` by default; use `none` (+ Secure) when the SPA and BFF are on **different origins**. |
| `AUTH_COOKIE_DOMAIN` | Optional explicit cookie domain. |
| `CORS_ORIGINS` | Set to the SPA origin(s) (not `*`) so `allow_credentials` is enabled for cookie auth. |

## Follow-ups

- **Session backend**: in-memory today (single instance). Back with Redis
  (`REDIS_URL` is already available) for multi-pod Cloud Foundry — the
  `SessionStore` interface is a drop-in.
- **Adopt the guard**: `auth_dependencies.get_current_user` is ready to replace
  the `X-User` header trust on protected controllers (SA-2532/SA-2533).
- **Token refresh**: `refresh_token` is stored; a silent-refresh path on
  near-expiry is a natural next step.
