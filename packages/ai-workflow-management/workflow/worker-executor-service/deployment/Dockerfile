FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
# python-qpid-proton ships no Linux wheels, so pip compiles it from the sdist,
# which needs a C toolchain + OpenSSL/FFI headers. Install the build deps,
# build, then purge them so the runtime image stays slim — the runtime shared
# libs (libssl3/libffi) are base-image packages and remain; only the compiler
# and -dev headers are removed.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential pkg-config libssl-dev libffi-dev; \
    pip install --no-cache-dir -r requirements.txt; \
    apt-get purge -y --auto-remove build-essential pkg-config libssl-dev libffi-dev; \
    rm -rf /var/lib/apt/lists/*
COPY . .

RUN adduser --disabled-password --gecos '' appuser
USER appuser

EXPOSE 8004
CMD ["python", "main.py"]
