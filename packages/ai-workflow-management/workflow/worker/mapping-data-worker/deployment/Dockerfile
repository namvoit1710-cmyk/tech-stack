# === Stage 1: build the native json-csv-streamer wheel (SA-1905) ===
# Streaming JSON->CSV producer (Rust/PyO3). Built against python:3.12-slim so
# the abi3 wheel is ABI-compatible with the runtime. The Rust toolchain stays in
# this builder stage; the runtime image gets only the compiled wheel. Needs
# build-time egress to PyPI/rust-lang/crates.io; a failure here fails the image
# build without affecting the running service.
FROM python:3.12-slim AS wheelbuilder
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl build-essential; \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable; \
    rm -rf /var/lib/apt/lists/*
ENV PATH="/root/.cargo/bin:${PATH}"
RUN pip install --no-cache-dir maturin
COPY --from=json_csv_streamer . /jcs
WORKDIR /jcs
RUN maturin build --release --out /wheels

# === Stage 2: runtime image ===
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app


COPY --from=worker-sdk . /worker-sdk

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# SA-1905 — install the native streaming wheel (built in Stage 1). Optional at
# runtime: the import is soft and WORKER_CHUNKED_OUTPUT_ENABLED is OFF by
# default, so the worker runs identically without it.
COPY --from=wheelbuilder /wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

COPY . .

RUN adduser --disabled-password --gecos '' appuser
USER appuser

EXPOSE 35000

CMD ["python", "main.py"]

# rebuild-marker: SA-1905 async-path streaming fix (2026-07-11)

# rebuild-marker: SA-1905 adversarial-review async-robustness fix (2026-07-11b)

# rebuild-marker: SA-1905 D2.4 worker paged file_ref fetch (2026-07-11c)
