"""Auto-register Push Gateway client implementations."""
from __future__ import annotations

import logging

from .push_gateway_registry import register_client, create_client, get_registry

logger = logging.getLogger(__name__)

# Always available
from .http_push_gateway_client import HttpPushGatewayClient
register_client("http", HttpPushGatewayClient)

# Optional: gRPC (requires grpcio + generated stubs)
try:
    from .grpc_push_gateway_client import GrpcPushGatewayClient
    register_client("grpc", GrpcPushGatewayClient)
except ImportError:
    logger.debug("gRPC Push Gateway client not available (missing grpcio or proto stubs)")
