# Deploying `governance-smart-api` (SAP BTP CloudFoundry)

Deployment scaffolding for the eagle Governance Smart API, mirroring
`apps/backend/agent/business-agent/deployment`. SA-1480.

## Artifacts

| File | Purpose |
|---|---|
| `Dockerfile` | `python:3.12-slim` image, non-root `appuser`, `CMD python main.py`. |
| `deployment.yml` | Deploy manifest consumed by the Jenkins/CF pipeline: image build contexts, per-env `memory`/`instances`/`env`. |
| `../.dockerignore` | Keeps `.env`, `.venv`, `tests/`, caches out of the build context/image. |
| `../env.example` | Every environment variable with its default; secrets left blank. |

## How the build context works

`requirements.txt` installs the SDK editable: `-e ../smart-service-sdk`. That
sibling dir lives outside this service's build context, so `deployment.yml`
exposes it as the named build context `smart-service-sdk`, and the `Dockerfile`
pulls it in with `COPY --from=smart-service-sdk . /smart-service-sdk` — the exact
path `-e ../smart-service-sdk` resolves to from `/app`.

> The build fetches a heavy ML stack (torch / sentence-transformers / spaCy +
> the `en_core_web_sm` model wheel). The build host needs outbound network and
> a few GB of image space; runtime memory is sized for it (`2048M` dev / `4096M`
> qas) because the embedding model loads into memory on startup.

## Prerequisites before the app goes healthy (read this)

The app's startup lifespan initializes the SDK container, which **connects to SAP
HANA**. A deploy will not become healthy until, per space, you provide:

```bash
# space-specific infra (not committed — set per space)
cf set-env governance-smart-api HANA_HOST <your-hana-cloud-host>
cf set-env governance-smart-api HANA_SCHEMA <your-schema>

# secrets (never commit these)
cf set-env governance-smart-api HANA_PASSWORD  <secret>
cf set-env governance-smart-api OPENAI_API_KEY <secret>   # blank => local stub LLM
```

`deployment.yml` intentionally leaves `HANA_HOST`/`HANA_SCHEMA` blank and omits
both secrets. Everything else has a working default (see `env.example`).

## Health endpoints

- `GET /health` → `{"status":"ok"}` — dependency-free liveness (use this as the
  CF health-check HTTP endpoint).
- `GET /api/v1/health` → `{"status":"ok","service":"ai-eagle"}` — SDK health.

`main.py` binds `0.0.0.0:$PORT` (CF injects `PORT`; defaults to `8080` locally).

## Verification status

| Acceptance criterion | Status |
|---|---|
| SDK editable dep resolves (`-e ../smart-service-sdk`) | ✅ verified — `import smart_service_sdk` OK against reconciled path |
| `GET /health` → `{"status":"ok"}` | ✅ verified in-process (TestClient) |
| `deployment.yml` has dev+qas memory/instances/env, no secrets | ✅ verified — YAML parses, secret values absent |
| `env.example` lists all variables | ✅ verified against the SDK `Settings` class |
| `docker build` succeeds | ⏳ CI-gated — no Docker daemon in the dev sandbox; Dockerfile mirrors the proven business-agent pattern |
| Deploys to CF `dev` and `/health` responds | ⏳ CI/CF-gated — run by the Jenkins → CF pipeline once HANA host + secrets are set |

## Deploy

Push to the pipeline the same way as other `apps/backend/*` services (the
Jenkins job reads `deployment.yml`, builds the image with the named build
contexts, and pushes to the target CF space).
