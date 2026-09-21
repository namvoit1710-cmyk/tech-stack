# LaidonLLM — Cloud Foundry deployment

Standard `deployment/` folder for the monorepo CI (`.github/workflows/build-image.yml`
→ `deploy.yml`). The pipeline auto-discovers this service via
`apps/**/deployment/deployment.yml`, builds `deployment/Dockerfile`, pushes the image
to `${REGISTRY}/backend/agent/laidonllm:<tag>`, and `cf push`es it using the shared
`deployment/manifest.yml` (Docker-image based).

| | |
|---|---|
| App / route name | `laidonllm` → `<org>-laidonllm.cfapps.br10.hana.ondemand.com` |
| Image path | `backend/agent/laidonllm` |
| Persistence | **SAP HANA** (`DB_BACKEND=hana`) — no Prisma/Postgres |
| Port | Cloud Foundry `$PORT` (entrypoint binds `0.0.0.0:$PORT`) |
| Schema | auto-provisioned on connect (idempotent, `HanaClient._ensure_schema`) |

## How it deploys
- **Auto:** push to `dev` / `main` / `solace` → *Build Image* → *Deploy* runs for changed
  services (laidonllm is included when its files change; releases/`force_all` deploy all).
- **Manual:** *Deploy* workflow_dispatch → set `app_name: laidonllm`, an `image_tag`, and env.

## Secrets (NOT committed — provided at runtime)
`deployment.yml` holds only non-secret config. The HANA connection and master key are
supplied one of two ways:

**A. Bind a HANA service (preferred).** Add the instance under `services:` in
`deployment.yml` (per-tier). Its `VCAP_SERVICES` credentials are mapped to
`HANA_HOST/PORT/USER/PASSWORD/SCHEMA` by `cf_entrypoint.sh` on boot. Then set only the
master key:
```
cf set-env laidonllm LITELLM_MASTER_KEY '<sk-...>'
cf restart laidonllm
```

**B. Explicit env (no binding).** Set everything via `cf set-env` (values take precedence
over VCAP):
```
cf set-env laidonllm HANA_HOST '<host>'
cf set-env laidonllm HANA_PORT '<port>'
cf set-env laidonllm HANA_USER '<user>'
cf set-env laidonllm HANA_PASSWORD '<password>'
cf set-env laidonllm HANA_SCHEMA '<schema>'
cf set-env laidonllm LITELLM_MASTER_KEY '<sk-...>'
cf restart laidonllm
```

`HANA_ENCRYPT=true` and the LiteLLM knobs are already set in `deployment.yml`.

## Files
- `deployment.yml` — CI discovery config (memory/disk/instances/env/services).
- `Dockerfile` — multi-stage LiteLLM+UI build with a CF/HANA runtime (build context `..`).
- `cf_entrypoint.sh` — maps bound HANA creds → `HANA_*`, starts the proxy on `$PORT`.
- `config.yaml` — proxy config (`store_model_in_db: true`; models managed at runtime in HANA).
