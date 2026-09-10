"""Application lifespan management (startup/shutdown)."""
import asyncio
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from container import Container
from app.layer4_infrastructure.logger.app_logger import get_logger

logger = get_logger(__name__)


def create_health_check_scheduler(container: Container) -> BackgroundScheduler:
    """Create and configure the periodic health check scheduler."""
    settings = container.settings()
    use_case = container.health_check_agents_use_case()

    def run_health_check_job() -> None:
        """Wrap the async health check job for synchronous execution.
        
        This function is called by the scheduler and runs outside of the FastAPI event loop, 
        so we use asyncio.run to execute the async use case.
        BackgroundScheduler ONLY supports synchronous jobs.
        """
        asyncio.run(use_case.execute())

    # BackgroundScheduler is suitable for running periodic jobs in a separate thread without blocking the main application
    # Health checks having synchronous database execution, using AsyncIOScheduler might cause unintended blocking issues if not carefully managed,
    # especially with SQLAlchemy sessions.
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_health_check_job,
        trigger="interval",
        seconds=settings.health_check_interval_seconds,
        id="health-check-agents",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Handles startup and shutdown logic:
    - Startup: Initialize database, create tables
    - Shutdown: Close database connections
    """
    # ========== Startup ==========
    logger.info("application_startup", message="Starting Agent Registry Service")
    
    # Get container
    container: Container = app.state.container
    settings = container.settings()
    
    # Initialize database
    db_factory = container.database_factory()
    scheduler: BackgroundScheduler | None = None
    
    try:
        # Create engine
        db_factory.create_engine()
        logger.info("database_engine_created", message="Database engine initialized")
        
        # Create tables — gated by DB_AUTO_CREATE (D26). Real environments are provisioned
        # by `alembic upgrade`; running create_all() on a migrated HANA schema re-issues
        # CREATE TABLE for existing tables and fails startup. Default OFF → rely on alembic.
        if settings.db_auto_create:
            db_factory.create_tables()
            logger.info("database_tables_created", message="Database tables created (DB_AUTO_CREATE=on)")
        else:
            logger.info(
                "database_tables_skipped",
                message="DB_AUTO_CREATE is off; schema is managed by alembic migrations (D26)",
            )
        
        # Create session factory
        db_factory.create_session_factory()
        logger.info("database_session_factory_created", message="Session factory created")

        # Start periodic health checks
        scheduler = create_health_check_scheduler(container)
        scheduler.start()
        app.state.health_check_scheduler = scheduler
        logger.info(
            "health_check_scheduler_started",
            message="Health check scheduler started",
            interval_seconds=settings.health_check_interval_seconds,
        )
        
    except Exception as e:
        logger.error(
            "application_startup_failed",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        raise
    
    logger.info("application_startup_complete", message="Application ready")
    
    yield  # Application runs here
    
    # ========== Shutdown ==========
    logger.info("application_shutdown", message="Shutting down Agent Registry Service")
    
    try:
        # Stop health check scheduler
        scheduler = getattr(app.state, "health_check_scheduler", scheduler)
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info(
                "health_check_scheduler_stopped",
                message="Health check scheduler stopped",
            )

        # Close external API clients
        user_info_client = container.fetch_current_user_info_api_client()
        await user_info_client.aclose()
        logger.info("user_info_api_client_closed", message="User info API client closed")

        workflow_client = container.fetch_workflow_api_client()
        await workflow_client.aclose()
        logger.info("workflow_api_client_closed", message="Workflow API client closed")

        # Close database connections
        db_factory.close()
        logger.info("database_connections_closed", message="Database connections closed")
        
    except Exception as e:
        logger.error(
            "application_shutdown_failed",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
    
    logger.info("application_shutdown_complete", message="Application stopped")
