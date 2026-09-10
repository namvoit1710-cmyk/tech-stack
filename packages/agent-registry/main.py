"""FastAPI application entry point."""
import uvicorn
from bootstrap import create_app

# Create FastAPI app
app = create_app()
settings = app.state.container.settings()

if __name__ == "__main__":
    # Run with uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=True,
        log_level="info",
    )
