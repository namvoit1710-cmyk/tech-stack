# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Build stage — bun workspace install + Node-based build for @ldc/workflow-management
# Build context MUST be apps/frontend/ so the workspace resolves correctly:
#   docker build -f apps/workflow-management/deployment/Dockerfile -t workflow-management .
# -----------------------------------------------------------------------------
FROM oven/bun:1.3.11 AS builder

WORKDIR /workspace

# Rsbuild/module federation is stable under Node in Linux; keep Bun for lockfile install.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/*

# Copy manifests first to maximize layer caching for `bun install`.
# bun.lock MUST be here: without it `--frozen-lockfile` below has nothing to
# freeze and silently resolves every range fresh, so each build floats onto
# whatever was published since (that is how axios 1.19.0 broke @ldc/api-sdk).
COPY package.json bun.lock turbo.json ./
COPY apps ./apps
COPY packages ./packages
COPY tooling ./tooling

RUN bun install --frozen-lockfile

# Copy the rest of the workspace (source + env files consumed by rsbuild).
COPY . .

RUN bun run build:workflow

# -----------------------------------------------------------------------------
# Runtime stage — nginx serving the static SPA bundle
# -----------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runner

RUN rm -rf /usr/share/nginx/html/* \
 && rm /etc/nginx/conf.d/default.conf

COPY --from=builder /workspace/apps/workflow-management/dist /usr/share/nginx/html
COPY apps/workflow-management/deployment/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD ["sh", "-c", "wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1"]

CMD ["nginx", "-g", "daemon off;"]