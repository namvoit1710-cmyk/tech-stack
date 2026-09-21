# syntax=docker/dockerfile:1.7
#
# LaidonLLM (LiteLLM proxy + Admin UI) on SAP HANA, for Cloud Foundry.
# Build context is the laidonllm app root (deployment.yml: buildContext: ..).
# Mirrors the repo-root LiteLLM Dockerfile's proven multi-stage build; the only
# differences are the runtime entrypoint (Cloud Foundry $PORT + HANA) — there is
# no Prisma migration on boot, since persistence is HANA (DB_BACKEND=hana) and the
# HanaClient auto-provisions its schema on connect.

ARG LITELLM_BUILD_IMAGE=cgr.dev/chainguard/wolfi-base@sha256:c61ac6919b811ea53c4782d69f1fe05218ba3c25d53f01b6ab7892e621bd4370
ARG LITELLM_RUNTIME_IMAGE=cgr.dev/chainguard/wolfi-base@sha256:c61ac6919b811ea53c4782d69f1fe05218ba3c25d53f01b6ab7892e621bd4370
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a
ARG UI_BUILD_IMAGE=node:20.18-alpine3.20@sha256:3488b10bf958af7125a176419d2d8a9937d895bf124012aae811651988d2ffe6

FROM $UV_IMAGE AS uvbin

# Admin UI builder (native on the build platform).
FROM --platform=$BUILDPLATFORM $UI_BUILD_IMAGE AS ui-builder
ENV NEXT_TELEMETRY_DISABLED=1 npm_config_fund=false npm_config_audit=false
WORKDIR /ui
COPY ui/litellm-dashboard/package.json ui/litellm-dashboard/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --prefer-offline
COPY ui/litellm-dashboard/ ./
# SA-2229: export into a guaranteed-empty out/. Next.js static export does not prune
# files left by a previous export, so any stale out/ reaching this stage yields a
# mixed-generation bundle - HTML from one build, _next/static chunks from another.
RUN rm -rf out && npm run build

# Builder stage
FROM $LITELLM_BUILD_IMAGE AS builder
WORKDIR /app
USER root
COPY --from=uvbin /uv /usr/local/bin/uv
COPY --from=uvbin /uvx /usr/local/bin/uvx

RUN apk add --no-cache bash gcc python3 python3-dev rust openssl openssl-dev nodejs npm libsndfile

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

# Dependency metadata first (layer caching). hdbcli (HANA driver) is a main
# dependency in pyproject.toml, so it is installed by the syncs below.
COPY pyproject.toml uv.lock ./
COPY enterprise/pyproject.toml enterprise/
COPY litellm-proxy-extras/pyproject.toml litellm-proxy-extras/

RUN uv sync --frozen --no-install-project --no-install-workspace --no-default-groups --no-editable \
    --extra proxy --extra proxy-runtime --extra extra_proxy --extra semantic-router --python python3

# Full source tree (includes litellm/proxy/db/hana/* + the committed Admin UI bundle)
COPY . .

# Replace the committed UI bundle with the one built from this exact source.
RUN rm -rf litellm/proxy/_experimental/out
COPY --from=ui-builder /ui/out/. litellm/proxy/_experimental/out/
RUN sed -i 's/\r$//' docker/build_admin_ui.sh && chmod +x docker/build_admin_ui.sh && ./docker/build_admin_ui.sh

# SA-2229 root cause: Turbopack hashes chunk names from an alphabet including `.`,
# so a hash can yield a basename containing `.wh.` (e.g. 05rzc7f~0d.wh.js). `.wh.`
# is the OverlayFS whiteout marker, so that file does not survive being baked into
# an image layer - the bundle is correct on the build host and arrives in the
# container with one chunk missing. Rename such names before they are layered.
RUN python3 docker/sanitize_ui_bundle.py litellm/proxy/_experimental/out

# SA-2229 gate: fail the build if any exported page references a _next/static asset
# the bundle does not contain. Without this, a mixed-generation bundle ships silently
# and one route (e.g. /ui/teams) dies at runtime with ChunkLoadError while its
# siblings work. Runs last, so it also covers build_admin_ui.sh's output.
RUN python3 docker/verify_ui_bundle.py litellm/proxy/_experimental/out

# Install the project + workspace packages into the venv (deps already cached).
RUN uv sync --frozen --no-default-groups --no-editable \
    --extra proxy --extra proxy-runtime --extra extra_proxy --extra semantic-router --python python3

# SA-2229 gate on the SHIPPED bundle (now FATAL).
#
# The runtime stage copies ONLY /app/.venv, so users are served the *installed* copy -
# not the source tree gated above. The two diverged once: a chunk whose name contained
# `.wh.` (the OverlayFS whiteout marker) was dropped while being layered, and the
# source-tree gate could not see it. This is the guard that can.
#
# Landed non-fatal while the mechanism was unknown; promoted to fatal now that
# sanitize_ui_bundle.py removes the cause. Two assertions:
#   1. the installed chunk set is the same SIZE as the source's (nothing lost in transit)
#   2. the installed bundle has no dangling references (the same check, on what ships)
RUN V="$(ls -d /app/.venv/lib/python*/site-packages | head -1)" && \
    SRC="litellm/proxy/_experimental/out" && \
    INST="$V/litellm/proxy/_experimental/out" && \
    src_n="$(ls "$SRC/_next/static/chunks/" | wc -l)" && \
    inst_n="$(ls "$INST/_next/static/chunks/" | wc -l)" && \
    echo "SA-2229 gate: source-chunk-count=$src_n installed-chunk-count=$inst_n" && \
    if [ "$src_n" != "$inst_n" ]; then \
      echo "FAIL: chunk count differs - $src_n built, $inst_n shipped." >&2; \
      echo "      Files present in the build but missing from the venv:" >&2; \
      ls "$SRC/_next/static/chunks/" | sort > /tmp/sa2229-src.txt; \
      ls "$INST/_next/static/chunks/" | sort > /tmp/sa2229-inst.txt; \
      comm -23 /tmp/sa2229-src.txt /tmp/sa2229-inst.txt >&2; \
      exit 1; \
    fi && \
    python3 docker/verify_ui_bundle.py "$INST"

# Runtime stage
FROM $LITELLM_RUNTIME_IMAGE AS runtime
USER root
RUN apk add --no-cache bash openssl tzdata python3 libsndfile

WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}" \
    DB_BACKEND=hana \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8

# The app is installed inside the venv (litellm incl. the HANA backend + packaged
# Admin UI). enterprise/ is imported by source path at runtime.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/enterprise /app/enterprise

RUN find /app/.venv -type f -path "*/tornado/test/*" -delete && \
    find /app/.venv -type d -path "*/tornado/test" -delete

# CF entrypoint (maps bound HANA creds, starts on $PORT) + proxy config.
COPY deployment/config.yaml /app/config.yaml
COPY deployment/cf_entrypoint.sh /app/cf_entrypoint.sh
RUN sed -i 's/\r$//' /app/cf_entrypoint.sh && chmod +x /app/cf_entrypoint.sh

# Cloud Foundry assigns $PORT (default 8080); the entrypoint binds to it.
EXPOSE 8080
ENTRYPOINT ["/app/cf_entrypoint.sh"]
