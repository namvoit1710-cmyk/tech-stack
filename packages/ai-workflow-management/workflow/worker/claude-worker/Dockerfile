# Claude Worker — Linux/WSL image.
#
# IMPORTANT: build with the PARENT `worker/` dir as the build context, because
# claude-worker depends on the sibling `worker-sdk` (requirements.txt: -e ../worker-sdk):
#
#     cd .../workflow/worker
#     docker build -f claude-worker/Dockerfile -t claude-worker:local .
#
# The worker shells out to the Claude Code CLI (`claude -p`) and to `git` (clone +
# open PR), so the image bundles Node.js + @anthropic-ai/claude-code and git.
# On Linux the CLI is invoked directly (the Windows `cmd /C` + kill-on-job paths
# in main.py / the use-case are skipped automatically).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps:
#   git          -> clone the repo + push the PR branch
#   curl, ca-*   -> fetch the NodeSource setup + TLS for git/github API
#   nodejs       -> runtime for the Claude Code CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
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
COPY claude-worker/ ./claude-worker/

# Install the SDK (editable) + the worker's runtime deps (httpx, playwright). Skip the
# test-only deps (pytest*) — they're not needed to run the worker.
#
# NOTE: this does NOT read requirements.txt, so a dep added there alone never reaches
# this image. Runtime deps must be listed HERE too.
RUN pip install --no-cache-dir -e ./worker-sdk \
    && pip install --no-cache-dir httpx playwright

# The browser for `claude_repro`, which reproduces a ticket on the running app (the PROVE
# step a code-read root cause cannot replace). `--with-deps` apt-installs the shared
# libraries headless Chromium needs on python:3.12-slim; without them the browser is
# present but every launch fails on a missing .so.
#
# This costs roughly 400 MB of image. It is a deliberate trade: the alternative is a
# separate browser service and a second deployment to keep in step. Only ONE browser is
# installed — `playwright install` with no argument would also pull Firefox and WebKit,
# which nothing here uses.
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# main.py imports `worker_sdk` (from worker-sdk/) and `app` (from claude-worker/).
ENV PYTHONPATH=/worker/worker-sdk:/worker/claude-worker \
    APP_MODE=PULL \
    CLAUDE_WORKER_SECRETS=/secrets/.smdg-worker-secrets.json \
    CLAUDE_WORKER_TRACE=/trace/claude-worker-trace.log \
    # (Lease renewal is set in the claude-worker's config.py — it auto-merges into the
    #  SDK settings singleton at startup, so no LEASE_* env is needed here.)
    # The worker runs as root in the container and drives `claude -p
    # --dangerously-skip-permissions`, which the CLI refuses under root UNLESS it's told
    # it's in a sandbox. The container IS a sandbox, so this is the intended bypass.
    IS_SANDBOX=1

WORKDIR /worker/claude-worker
CMD ["python", "main.py"]
