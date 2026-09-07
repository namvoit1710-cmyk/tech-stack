from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.layer4_frameworks.config.app_config import settings


class DatabaseSessionManager:
    """Manages the SQLAlchemy Engine and Session Factory lifecycle."""
    def __init__(self):
        self.engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DB_ECHO,
            future=True
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self.session_factory


# Global instances
Base = declarative_base()
db_manager = DatabaseSessionManager()
