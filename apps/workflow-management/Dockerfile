# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Build stage — bun workspace install + Node-based build for @ldc/workflow-management
# Build context MUST be apps/frontend-v2/ so the workspace resolves correctly:
#   docker build -f apps/workflow-management/Dockerfile -t workflow-management .
# -----------------------------------------------------------------------------
FROM oven/bun:1.3.11 AS builder

WORKDIR /workspace

ARG PUBLIC_URL_API_INTEGRATION

# Rsbuild/module federation is stable under Node in Linux; keep Bun for lockfile install.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/*

# Copy manifests first to maximize layer caching for `bun install`.
COPY package.json turbo.json ./
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
COPY apps/workflow-management/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
