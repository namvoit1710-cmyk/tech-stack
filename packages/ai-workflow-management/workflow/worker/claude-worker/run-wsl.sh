#!/usr/bin/env bash
# Run the claude-worker container in WSL (PULL mode, against the tenant-1 executor).
# Usage (inside WSL, as root — docker needs root on this box):
#     wsl -u root
#     bash /mnt/c/Users/FelixDo/simplemdg_ai/apps/backend/ai-workflow-management/workflow/worker/claude-worker/run-wsl.sh
set -euo pipefail

REGISTRY_URL="${REGISTRY_URL:-https://smdg-ai-tenant-1-ai-workflow-worker-executor.cfapps.br10.hana.ondemand.com}"
SECRETS_HOST="${SECRETS_HOST:-/mnt/c/Users/FelixDo/.smdg-worker-secrets.json}"
TRACE_DIR_HOST="${TRACE_DIR_HOST:-/mnt/c/Users/FelixDo/Desktop}"

# The base-image pull and runtime egress fail over IPv6 on this box — force IPv4.
sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true

docker rm -f claude-worker >/dev/null 2>&1 || true
exec docker run --rm -it --name claude-worker \
  -e APP_MODE=PULL \
  -e WORKER_TENANT= \
  -e REGISTRY_URL="$REGISTRY_URL" \
  -v "$SECRETS_HOST":/secrets/.smdg-worker-secrets.json:ro \
  -v "$TRACE_DIR_HOST":/trace \
  claude-worker:local
