#!/usr/bin/env bash
# Generate Python gRPC stubs from .proto files.
#
# Usage:
#   cd workflow/proto && bash generate.sh
#
# Prerequisites:
#   pip install grpcio-tools protobuf

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROTO_ROOT="$SCRIPT_DIR"
OUT_DIR="$SCRIPT_DIR/generated"

# Clean previous output
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/workflow/v1"

# Create __init__.py files for Python package structure
touch "$OUT_DIR/__init__.py"
touch "$OUT_DIR/workflow/__init__.py"
touch "$OUT_DIR/workflow/v1/__init__.py"

# Compile all .proto files
python3 -m grpc_tools.protoc \
  --proto_path="$PROTO_ROOT" \
  --python_out="$OUT_DIR" \
  --grpc_python_out="$OUT_DIR" \
  --pyi_out="$OUT_DIR" \
  "$PROTO_ROOT"/workflow/v1/*.proto

# Fix relative imports in generated code (grpc_tools generates non-package imports).
# Replace "from workflow.v1 import" with "from .".
# Replace "import workflow.v1." with "from . import".
find "$OUT_DIR/workflow/v1" -name '*.py' -exec sed -i \
  -e 's/^from workflow\.v1 import/from . import/' \
  -e 's/^import workflow\.v1\.\(.*\)_pb2/from . import \1_pb2/' \
  {} +

echo "Generated stubs in $OUT_DIR"
ls -la "$OUT_DIR/workflow/v1/"
