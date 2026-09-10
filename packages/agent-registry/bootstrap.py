"""Application bootstrap and initialization."""
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.layer4_infrastructure.logger.app_logger import get_logger, setup_logging
from app.layer4_infrastructure.middleware.error_handlers import (
    domain_exception_handler,
    validation_exception_handler,
    generic_exception_handler,
)
from app.layer4_infrastructure.middleware.request_id import RequestIDMiddleware
from app.layer4_infrastructure.middleware.rate_limit import RateLimitMiddleware
from app.layer4_infrastructure.middleware.auth import AuthMiddleware
from app.layer1_domain.exceptions import DomainException
from container import Container
from lifespan import lifespan

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        FastAPI: Configured FastAPI application instance
    """

    # Create FastAPI app
    app = FastAPI(
        title="Agent Registry Service",
        description="Central registry for managing business and technical agents",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # Initialize container
    container = Container()
    app.state.container = container
    settings = container.settings()

    # Setup structured logging
    setup_logging(settings)

    # Middlewares. Starlette applies middleware in REVERSE registration order: the LAST
    # add_middleware call is the OUTERMOST layer (see Starlette build_middleware_stack).
    # We register CORS LAST so it is the outermost user middleware and handles the preflight
    # OPTIONS before the others. NOTE: Starlette's ServerErrorMiddleware still sits above ALL
    # user middleware, so a true 500 is produced outside CORS -- that CORS-header gap is not
    # addressed here (the origin/credentials config below is the actual browser fix).

    # 1. Rate limiting (innermost cross-cutting middleware)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=100,  # 100 requests per minute
        window_seconds=60,
    )

    # 2. Request ID tracking (for all requests)
    app.add_middleware(RequestIDMiddleware)

    # 3. Authentication (reads Authorization -> request.state.jwt_token)
    app.add_middleware(AuthMiddleware)

    # 4. CORS -- registered LAST so it is the OUTERMOST user middleware.
    # DEV/TEST ONLY (no prod env yet): allow origins per API_CORS_ORIGINS (default ["*"]) with
    # allow_credentials=False, which the browser accepts alongside a wildcard origin. This
    # unblocks the FE dev origin (localhost), which authenticates with a bearer token
    # (withCredentials:false), so no cookie/credentials are involved.
    # WARNING -- BEFORE ANY DEPLOYED ENV (dev/qas/prod): a deployed FE path uses cookie auth
    # (withCredentials:true), and dropping the origin allowlist here opens the service to any
    # origin. Credentialed requests REQUIRE allow_credentials=True + a SPECIFIC-origin allowlist
    # (the browser forbids "*" together with credentials). Re-enable allow_credentials=True and
    # set API_CORS_ORIGINS to explicit FE origins per env before any real deployment. See SA-2307.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register exception handlers
    app.add_exception_handler(DomainException, domain_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # Wire dependencies
    container.wire(
        modules=[
            "app.layer3_presentation.apis.v1.agents",
            "app.layer3_presentation.apis.v1.tools",
            "app.layer3_presentation.apis.v1.workflows",
            "app.layer3_presentation.apis.v1.users",
            "app.layer3_presentation.apis.v1.roles",
            "app.layer3_presentation.apis.v1.permissions",
            "app.layer3_presentation.apis.v1.agent_pools",
            "app.layer3_presentation.dependencies.principal",
            "app.layer3_presentation.dependencies.agent_visibility",
        ]
    )

    # Include routers
    from app.layer3_presentation.apis.v1.agents import router as agents_router
    from app.layer3_presentation.apis.v1.tools import router as tools_router
    from app.layer3_presentation.apis.v1.workflows import router as workflows_router
    from app.layer3_presentation.apis.v1.users import router as users_router
    from app.layer3_presentation.apis.v1.roles import router as roles_router
    from app.layer3_presentation.apis.v1.permissions import router as permissions_router
    from app.layer3_presentation.apis.v1.agent_pools import router as agent_pools_router
    app.include_router(agents_router)
    app.include_router(tools_router)
    app.include_router(workflows_router)
    app.include_router(users_router)
    app.include_router(roles_router)
    app.include_router(permissions_router)
    app.include_router(agent_pools_router)

    # Health check endpoint
    @app.get("/health", tags=["health"])
    async def health_check():
        """Service health check endpoint."""
        return {
            "status": "healthy",
            "service": "agent-registry-service",
            "version": "1.0.0",
        }

    logger.info("application_initialized", message="FastAPI application created")

    return app
