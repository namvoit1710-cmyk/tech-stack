# Governance Smart API — container image for SAP BTP CloudFoundry.
# Mirrors apps/backend/agent/business-agent/deployment/Dockerfile.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Editable sibling dependency declared in requirements.txt, placed at the
# absolute path its relative reference resolves to from /app:
#   `-e ../smart-service-sdk`  ->  /smart-service-sdk
# Supplied as the named build context `smart-service-sdk` in deployment.yml.
COPY --from=smart-service-sdk . /smart-service-sdk

# The SDK pulls a heavy ML stack (torch / sentence-transformers / spaCy + the
# en_core_web_sm model wheel, hdbcli, pdfplumber). build-essential covers any
# dependency that ships only an sdist for cp312. Network is required at build
# time to fetch the spaCy model wheel referenced in the SDK's pyproject.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos '' appuser
USER appuser

# Local/default port. CloudFoundry injects $PORT at runtime; main.py honors it.
EXPOSE 8080

CMD ["python", "main.py"]
