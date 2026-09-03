#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_ROOT="$(dirname "$SCRIPT_DIR")"

python3 -m grpc_tools.protoc \
  -I"$SDK_ROOT" \
  --python_out="$SDK_ROOT" \
  --grpc_python_out="$SDK_ROOT" \
  "$SDK_ROOT/agent_sdk/layer4_frameworks/messaging/protos/push_gateway.proto"
