from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.layer1_domain.exceptions import AppError
from app.layer3_adapters.controllers.restful.v1.middleware.error_handler import (
    app_error_handler,
    generic_error_handler,
    optimistic_concurrency_handler,
)
from app.layer2_application.repositories.run_repository_interface import (
    OptimisticConcurrencyError,
)
from app.layer3_adapters.controllers.restful.v1.middleware.etag import ETagMiddleware
from app.layer3_adapters.controllers.restful.v1.middleware.auth_enforcement import (
    AuthEnforcementMiddleware,
)
from app.layer3_adapters.controllers.restful.v1.middleware.response_wrapper import (
    DEFAULT_IGNORE_PATHS,
    ResponseWrapperMiddleware,
)
from app.layer3_adapters.controllers.restful.v1.health_controller import router as health_router
from app.layer3_adapters.controllers.restful.v1.run_controller import router as run_router
from app.layer3_adapters.controllers.restful.v1.workflow_controller import router as workflow_router
from app.layer3_adapters.controllers.restful.v1.output_controller import router as output_router
from app.layer3_adapters.controllers.restful.v1.node_palette_controller import router as node_palette_router
from app.layer3_adapters.controllers.restful.v1.node_catalog_controller import router as node_catalog_router
from app.layer3_adapters.controllers.restful.v1.worker_replicas_controller import router as worker_replicas_router
from app.layer3_adapters.controllers.restful.v1.worker_apps_controller import router as worker_apps_router
from app.layer3_adapters.controllers.restful.v1.metrics_controller import router as metrics_router
from app.layer3_adapters.controllers.restful.v1.node_definition_controller import router as node_definition_router
from app.layer3_adapters.controllers.restful.v1.socketio_controller import router as socketio_router
from app.layer3_adapters.controllers.restful.v1.worker_function_controller import router as worker_function_router
from app.layer3_adapters.controllers.restful.v1.expression_controller import router as expression_router
from app.layer3_adapters.controllers.restful.v1.webhook_controller import router as webhook_router
from app.layer3_adapters.controllers.restful.v1.webhook_management_controller import router as webhook_management_router
from app.layer3_adapters.controllers.restful.v1.trigger_registration_controller import router as trigger_registration_router
from app.layer3_adapters.controllers.restful.v1.internal_function_controller import router as internal_function_router
from app.layer3_adapters.controllers.restful.v1.approval_controller import router as approval_router
from app.layer3_adapters.controllers.restful.v1.auth_controller import router as auth_router
from app.layer3_adapters.controllers.restful.v1.gateway_app_controller import (
    router as gateway_app_router,
)
from app.layer3_adapters.controllers.restful.v1.gateway_function_controller import (
    router as gateway_function_router,
)
from app.layer3_adapters.controllers.restful.v1.gateway_resolve_controller import (
    router as gateway_resolve_router,
)
from app.layer3_adapters.controllers.restful.v1.environment_controller import (
    router as environment_router,
)
from app.layer3_adapters.controllers.restful.v1.workflow_environment_controller import (
    router as workflow_environment_router,
)
from app.layer3_adapters.controllers.restful.v1.env_section_schema_controller import (
    router as env_section_schema_router,
)
from app.layer3_adapters.controllers.restful.v1.workflow_section_controller import (
    router as workflow_section_router,
)
from app.layer3_adapters.controllers.restful.v1.section_membership_controller import (
    router as section_membership_router,
)
from app.layer3_adapters.controllers.restful.v1.user_controller import (
    router as user_router,
)
from app.layer3_adapters.controllers.restful.v1.analytics_controller import (
    router as analytics_router,
)
from app.layer4_frameworks.config.app_config import settings
from typing import Any


def create_fastapi_app(container: dict, lifespan: Any=None) -> FastAPI:
    app = FastAPI(title="Workflow Control Plane", version="0.2.0", lifespan=lifespan)

    # Store container for Depends()-based injection in controllers
    app.state.container = container

    # CORS — parse configured origins
    raw_origins = settings.CORS_ORIGINS.strip()
    if raw_origins == "*":
        origins = ["*"]
        allow_credentials = False
    else:
        origins = [o.strip() for o in raw_origins.split(",") if o.strip()]
        allow_credentials = True

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # SA-2533 — enforce a valid session/JWT on every /api/v1/* call (except the
    # auth + health routes) when AUTH_ENABLED. No-op when auth is off.
    app.add_middleware(AuthEnforcementMiddleware)

    # Middleware registration order: last-added is outermost.
    # ResponseWrapperMiddleware (inner) wraps JSON bodies in {status, data}.
    # ETagMiddleware (outer) hashes the already-wrapped body so clients
    # can round-trip If-None-Match against what they actually received.
    api_prefixes = ["/api/v1/", "/api/v2/"]
    app.add_middleware(
        ResponseWrapperMiddleware,
        wrap_prefixes=api_prefixes,
        # SA-2047 R10: /api/v2/worker-replicas must reach the FE as a bare
        # JSON array (apiV2.get<T[]>(...)), not the {status, data} envelope
        # every other /api/v1|v2 route gets. Carve it out here rather than
        # in the middleware itself so the wrapper's default behavior stays
        # the house convention for everything else.
        # SA-2047 R14: /api/v2/worker-apps needs the identical bare-array
        # carve-out for the same reason (see worker_apps_controller.py).
        ignore_paths=[
            *DEFAULT_IGNORE_PATHS,
            "/api/v2/worker-replicas",
            "/api/v2/worker-apps",
            # SA-2530 — the Keycloak BFF endpoints emit redirects (/login,
            # /callback) and a raw user object (/me), never the {status,data}
            # envelope.
            "/api/v1/auth",
        ],
    )
    if settings.ENABLE_NODE_CATALOG_V2:
        app.add_middleware(ETagMiddleware, path_prefixes=["/api/v2/node-catalog"])

    # Error handlers — more specific first so FastAPI picks the narrowest.
    app.add_exception_handler(
        OptimisticConcurrencyError, optimistic_concurrency_handler,
    )
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)

    # All v1 routes are mounted under /api/v1
    api_v1 = APIRouter()
    api_v1.include_router(health_router, prefix="/health", tags=["health"])
    api_v1.include_router(run_router, prefix="/runs", tags=["runs"])
    api_v1.include_router(workflow_router, prefix="/workflows", tags=["workflows"])
    api_v1.include_router(output_router, prefix="/outputs", tags=["outputs"])
    api_v1.include_router(node_palette_router, prefix="/node-palette", tags=["node-palette"])
    api_v1.include_router(node_definition_router, prefix="/node-definitions", tags=["node-definitions"])
    api_v1.include_router(socketio_router, prefix="/events", tags=["events"])
    api_v1.include_router(worker_function_router, prefix="/workers", tags=["workers"])
    api_v1.include_router(expression_router, prefix="/expressions", tags=["expressions"])
    api_v1.include_router(webhook_router, prefix="/webhooks", tags=["webhooks"])
    api_v1.include_router(webhook_management_router, prefix="/webhook-management", tags=["webhook-management"])
    api_v1.include_router(trigger_registration_router, prefix="/trigger-registrations", tags=["trigger-registrations"])
    api_v1.include_router(internal_function_router, prefix="/internal-functions", tags=["internal-functions"])
    api_v1.include_router(approval_router, prefix="/approval-process", tags=["approval"])
    # SA-2530 — Keycloak BFF (login/callback/logout/me). Active only when
    # settings.AUTH_ENABLED; otherwise every route returns 404.
    api_v1.include_router(auth_router, prefix="/auth", tags=["auth"])
    api_v1.include_router(environment_router, prefix="/environments", tags=["environments"])
    # SA-2471 — section-level shared variable key schema.
    api_v1.include_router(env_section_schema_router, prefix="/env-sections", tags=["env-sections"])
    # SA-2576: known-users registry (owner picker source).
    api_v1.include_router(user_router, prefix="/users", tags=["users"])
    api_v1.include_router(analytics_router, prefix="/analytics", tags=["analytics"])
    # SA-2450 — workflow↔section binding lives under /workflows alongside the
    # workflow controller (GET/PUT /workflows/{id}/environment-assignment).
    api_v1.include_router(workflow_environment_router, prefix="/workflows", tags=["workflow-environments"])
    # SA-2605: workflow→section binding + Section↔Environment membership (M:N)
    api_v1.include_router(workflow_section_router, prefix="/workflows", tags=["workflow-section"])
    api_v1.include_router(section_membership_router, prefix="/sections", tags=["section-membership"])
    api_v1.include_router(
        gateway_app_router,
        prefix="/gateway/apps",
        tags=["gateway-apps"],
    )
    # Functions are nested under their app (app addressed by name).
    api_v1.include_router(
        gateway_function_router,
        prefix="/gateway/apps/{app_name}/functions",
        tags=["gateway-functions"],
    )
    # Worker-facing resolve (by function id) — returns the decrypted call-spec.
    api_v1.include_router(
        gateway_resolve_router,
        prefix="/gateway/functions",
        tags=["gateway-resolve"],
    )

    app.include_router(api_v1, prefix="/api/v1")

    # v2 routes. node-catalog stays gated on ENABLE_NODE_CATALOG_V2 (default
    # off); worker-replicas (SA-2047 R10) and worker-apps (SA-2047 R14) are
    # NOT gated on that flag — they're unrelated features — so api_v2 itself
    # is mounted unconditionally.
    api_v2 = APIRouter()
    if settings.ENABLE_NODE_CATALOG_V2:
        api_v2.include_router(node_catalog_router, tags=["node-catalog"])
    api_v2.include_router(worker_replicas_router, tags=["worker-replicas"])
    api_v2.include_router(worker_apps_router, tags=["worker-apps"])
    app.include_router(api_v2, prefix="/api/v2")

    # Prometheus scrape endpoint — top-level /metrics path (conventional).
    # Not wrapped by ResponseWrapperMiddleware (emits text/plain, not JSON).
    app.include_router(metrics_router, prefix="/metrics", tags=["metrics"])

    return app
