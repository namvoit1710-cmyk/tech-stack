# SMDG CS Worker — Linux image.
#
# IMPORTANT: build with the PARENT `worker/` dir as the build context, because the worker
# depends on the sibling `worker-sdk`:
#
#     cd .../workflow/worker
#     docker build -f cs-worker/Dockerfile -t cs-worker:local .
#
# Or use cs-worker/docker-compose.yml, which sets the context for you.
#
# Node.js is here for ONE reason: the Claude Code CLI, which `cs_detect_request` and
# `cs_summarize_log` shell out to. Nothing else in this image needs it.
#
# What this image deliberately does NOT carry, unlike the claude-worker's: git (no repo is
# ever cloned) and headless Chromium via Playwright (nothing is reproduced in a browser).
# That is ~400 MB and a `playwright install --with-deps` on every rebuild, for capabilities
# a customer-service log lookup does not use.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# curl + ca-certificates fetch the NodeSource setup and give the CLI its TLS roots;
# curl is purged afterwards so it is not left in the running image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /worker

# Copy both packages (build context = the worker/ parent dir).
COPY worker-sdk/ ./worker-sdk/
COPY cs-worker/ ./cs-worker/

# Install the SDK (editable). Its pyproject pulls fastapi/pydantic/pydantic-settings/httpx,
# which is everything this worker needs at runtime — the nodes themselves use only stdlib.
#
# NOTE: this does NOT read cs-worker/requirements.txt, so a dependency added there alone
# never reaches this image. Runtime dependencies must be listed HERE too.
RUN pip install --no-cache-dir -e ./worker-sdk

# main.py imports `worker_sdk` (from worker-sdk/) and `app` (from cs-worker/).
ENV PYTHONPATH=/worker/worker-sdk:/worker/cs-worker \
    APP_MODE=PULL \
    WORKER_TENANT="" \
    REGISTRY_URL=https://smdg-ai-tenant-1-ai-workflow-worker-executor.cfapps.br10.hana.ondemand.com \
    LOG_WORKER_TRACE_FILE=/tmp/cs-worker-trace.log \
    # The worker runs as root in the container. The Claude CLI refuses to run as root
    # unless it is told it is in a sandbox — the container IS one, so this is the
    # intended bypass, same as the claude-worker's image.
    IS_SANDBOX=1

WORKDIR /worker/cs-worker
CMD ["python", "main.py"]
