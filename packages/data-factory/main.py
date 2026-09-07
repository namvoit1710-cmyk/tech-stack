from contextlib import asynccontextmanager
import sys
import os
import uvicorn
import psutil
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.layer4_frameworks.config.app_config import settings
from app.layer1_domain.exceptions import (
    DataFactoryError,
    ValidationError,
    NotFoundError,
    ConflictError,
    AuthorizationError,
    OperationTimeoutError,
    ExternalServiceError,
)
from app.layer4_frameworks.orm.config.database_config import db_manager, Base
from app.layer4_frameworks.providers.adaptive_batching import compute_current_batch_size, _get_system_memory
from bootstrap import build_app_container
from app.layer3_adapters.controllers.restful.v1 import (
    data_validation_controller,
    data_transformation_controller,
    schema_transform_controller,
    rule_management_controller,
    rule_catalog_controller,
    reference_data_controller,
    bundle_controller,
    data_migration_controller,
)

# Add libs/mcp/python to path (local dev: ../../.., Docker: ./libs/mcp/python)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "libs", "mcp", "python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "libs", "mcp", "python"))

try:
    from app.layer3_adapters.controllers.mcp.tools import mcp as mcp_server, set_container
    from simplemdg_mcp import mount_mcp
    MCP_AVAILABLE = True
except ImportError as e:
    print(f"⚠️  MCP not available (missing dependency): {e}")
    print("   REST API will start without MCP endpoints.")
    MCP_AVAILABLE = False
    mcp_server = None
    set_container = None
    mount_mcp = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Init Database using Manager
    async with db_manager.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # 2. Build Dependency Injection Container
    app.state.container = await build_app_container()

    # 3. Inject container into MCP tools (if MCP is available)
    if MCP_AVAILABLE:
        set_container(app.state.container)

    print(f"🚀 {settings.APP_NAME} Started Successfully")
    if MCP_AVAILABLE:
        print(f"📡 MCP Server available at /mcp (SSE + Streamable HTTP)")
    else:
        print(f"⚠️  MCP endpoints not mounted (missing dependency)")
    yield
    print("🛑 Shutting down...")

app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# Restrict CORS to an explicit env-configured allow-list (never wildcard).
# When no origins are configured, no cross-origin access is granted.
_cors_origins = settings.cors_allow_origins_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )


# Map domain exceptions to transport-safe responses. Inner layers raise these;
# the framework layer decides status codes and returns a consistent error shape
# without leaking stack traces or internals.
_DOMAIN_ERROR_STATUS = [
    (ValidationError, 400),
    (AuthorizationError, 403),
    (NotFoundError, 404),
    (ConflictError, 409),
    (OperationTimeoutError, 504),
    (ExternalServiceError, 502),
]


def _status_for_domain_error(exc: DataFactoryError) -> int:
    for error_type, status_code in _DOMAIN_ERROR_STATUS:
        if isinstance(exc, error_type):
            return status_code
    return 400


@app.exception_handler(DataFactoryError)
async def handle_domain_error(request: Request, exc: DataFactoryError):
    return JSONResponse(
        status_code=_status_for_domain_error(exc),
        content={
            "success": False,
            "error_code": type(exc).__name__,
            "message": str(exc),
        },
    )


def _is_memory_available_for_request(path: str) -> bool:
    if not settings.MEMORY_GUARD_ENABLED:
        return True

    # Allow health checks even under pressure so orchestration can probe status.
    if path == "/health":
        return True

    # Apply guard to API routes where requests can trigger heavy memory work.
    if not path.startswith("/api/"):
        return True

    mem = _get_system_memory()
    available_percent = mem["available_percent"]
    available_mb = mem["available_mb"]

    if available_percent < settings.MEMORY_GUARD_MIN_AVAILABLE_PERCENT:
        return False
    if available_mb < settings.MEMORY_GUARD_MIN_AVAILABLE_MB:
        return False
    return True


@app.middleware("http")
async def memory_pressure_guard(request: Request, call_next):
    if not _is_memory_available_for_request(request.url.path):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Server busy: insufficient available memory. Please retry shortly.",
                "error_code": "SERVER_BUSY_MEMORY_PRESSURE",
            },
            headers={"Retry-After": "5"},
        )
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        import gc
        gc.collect()
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6")
            libc.malloc_trim(0)
        except Exception:
            pass
    return response

# Register REST Routers
app.include_router(data_validation_controller.router, prefix="/api/v1/validation", tags=["Validation"])
app.include_router(data_transformation_controller.router, prefix="/api/v1/transformation", tags=["Transformation"])
app.include_router(schema_transform_controller.router, prefix="/api/v1/schema-transform", tags=["Schema Transform"])
app.include_router(rule_management_controller.router, prefix="/api/v1/rules", tags=["Rules"])
app.include_router(rule_catalog_controller.router, prefix="/api/v1/rule-types", tags=["Rule Types"])
app.include_router(reference_data_controller.router, prefix="/api/v1/reference", tags=["Reference Data"])
app.include_router(bundle_controller.router, prefix="/api/v1/bundle", tags=["Bundle"])
app.include_router(data_migration_controller.router, prefix="/api/v1/data-migration", tags=["Data Migration"])

# Mount MCP Server (SSE + Streamable HTTP, dynamic transport)
if MCP_AVAILABLE:
    mount_mcp(app, mcp_server)

@app.get("/health")
def health_check():
    mem = _get_system_memory()
    current_batch = compute_current_batch_size(total_rows=1_000_000)
    return {
        "status": "healthy",
        "architecture": "Clean 4-Layer",
        "mcp": MCP_AVAILABLE,
        "batch_size": current_batch,
        "memory": {
            "available_percent": round(mem["available_percent"], 2),
            "available_mb": round(mem["available_mb"], 2),
            "total_mb": round(mem["total"] / (1024 * 1024), 2),
            "used_mb": round(mem["current"] / (1024 * 1024), 2),
            "guard_enabled": settings.MEMORY_GUARD_ENABLED,
            "guard_threshold_percent": settings.MEMORY_GUARD_MIN_AVAILABLE_PERCENT,
            "guard_threshold_mb": settings.MEMORY_GUARD_MIN_AVAILABLE_MB,
        },
    }

@app.get("/debug/mem")
def debug_mem():
    import gc
    import polars as pl
    import psutil
    import sys
    
    gc.collect()
    
    type_counts = {}
    type_sizes = {}
    
    for obj in gc.get_objects():
        try:
            t = type(obj).__name__
            size = sys.getsizeof(obj)
            type_counts[t] = type_counts.get(t, 0) + 1
            type_sizes[t] = type_sizes.get(t, 0) + size
        except Exception:
            pass
            
    sorted_types = sorted(type_sizes.items(), key=lambda x: x[1], reverse=True)[:20]
    
    p = psutil.Process()
    rss = p.memory_info().rss / (1024 * 1024)
    
    return {
        "rss_mb": rss,
        "top_types_by_size": [{ "type": t, "count": type_counts[t], "size_kb": round(s/1024, 2) } for t, s in sorted_types],
        "gc_garbage": len(gc.garbage)
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
