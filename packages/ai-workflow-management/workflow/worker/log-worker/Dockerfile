# SMDG Log Worker — Linux image.
#
# The nodes are pure HTTP/OAuth (RequestActionLog / SubmitEventLog / RequestLog /
# Cloud Logging), so the image needs NO Node.js, git or Claude CLI — just Python + the SDK.
#
# IMPORTANT: build with the PARENT `worker/` dir as the build context, because the
# worker depends on the sibling `worker-sdk` (requirements.txt: -e ../worker-sdk):
#
#     cd .../workflow/worker
#     docker build -f log-worker/Dockerfile -t log-worker:local .
#
# Or just use log-worker/docker-compose.yml (which sets the context for you).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /worker

# Copy both packages (build context = the worker/ parent dir).
COPY worker-sdk/ ./worker-sdk/
COPY log-worker/ ./log-worker/

# Install the SDK (editable). Its pyproject pulls fastapi/pydantic/pydantic-settings/httpx,
# which is everything this worker needs at runtime (the nodes themselves use only stdlib).
RUN pip install --no-cache-dir -e ./worker-sdk

# main.py imports `worker_sdk` (from worker-sdk/) and `app` (from log-worker/).
ENV PYTHONPATH=/worker/worker-sdk:/worker/log-worker \
    APP_MODE=PULL \
    WORKER_TENANT="" \
    REGISTRY_URL=https://smdg-ai-tenant-1-ai-workflow-worker-executor.cfapps.br10.hana.ondemand.com

WORKDIR /worker/log-worker
CMD ["python", "main.py"]
