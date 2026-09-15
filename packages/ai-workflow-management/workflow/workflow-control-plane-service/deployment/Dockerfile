# === Stage 1: generate gRPC stubs from .proto sources ===
FROM python:3.12-slim AS proto-builder

WORKDIR /protogen
# Pin grpcio-tools BELOW 1.82 so the generated *_pb2_grpc stubs stay compatible
# with the runtime grpcio (requirements pins <1.82 too). grpcio-tools 1.82.0
# generates stubs that hard-require grpcio>=1.82.0 at import; the runtime image
# resolves grpcio to 1.81.x, so an unpinned build crashes on startup with
# "generated code depends on grpcio>=1.82.0". Keep both bounds in lockstep.
RUN pip install --no-cache-dir "grpcio-tools>=1.60.0,<1.82.0" "protobuf>=4.25.0"

COPY --from=proto_src . /protogen/src/workflow

RUN mkdir -p /out/workflow_proto/v1 /tmp/raw \
    && touch /out/workflow_proto/__init__.py /out/workflow_proto/v1/__init__.py \
    && python -m grpc_tools.protoc \
         --proto_path=/protogen/src \
         --python_out=/tmp/raw \
         --grpc_python_out=/tmp/raw \
         --pyi_out=/tmp/raw \
         /protogen/src/workflow/v1/*.proto \
    && cp /tmp/raw/workflow/v1/*.py /out/workflow_proto/v1/ \
    && (cp /tmp/raw/workflow/v1/*.pyi /out/workflow_proto/v1/ 2>/dev/null || true) \
    && sed -i 's/^from workflow\.v1 import/from . import/' /out/workflow_proto/v1/*.py \
    && sed -i 's/^import workflow\.v1\.\(.*\)_pb2/from . import \1_pb2/' /out/workflow_proto/v1/*.py

# === Stage 1b: build the native json-csv-streamer wheel (SA-1905) ===
# Streaming JSON->CSV producer (Rust/PyO3). Built against the same
# python:3.12-slim so the abi3 wheel is ABI-compatible with the runtime. The
# Rust toolchain lives ONLY in this builder stage — the runtime image gets just
# the compiled wheel. maturin/rustup/cargo need egress to PyPI/rust-lang/
# crates.io at build time; if that's unavailable the build fails here (the
# running service is unaffected — a failed build simply deploys no new image).
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

# SA-1905 — install the native streaming wheel (built in Stage 1b). Optional at
# runtime: the import is soft and WORKER_CHUNKED_OUTPUT_ENABLED is OFF by
# default, so the service runs identically without it; present so the streaming
# path can be enabled per-tenant.
COPY --from=wheelbuilder /wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

COPY . .
COPY --from=proto-builder /out/workflow_proto /app/workflow_proto

RUN adduser --disabled-password --gecos '' appuser
USER appuser

EXPOSE 8001 50051

CMD ["python", "main.py"]
