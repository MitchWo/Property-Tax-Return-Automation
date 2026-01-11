"""Main FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router, web_router
from app.api.transaction_routes import transaction_router, transaction_web_router
from app.api.categorization_analytics import router as categorization_router, web_router as categorization_web_router
from app.api.skill_learning_routes import router as skill_learning_router
from app.api.workings_routes import router as workings_router
from app.config import settings
from app.database import init_db, AsyncSessionLocal
from app.services.seed_data import seed_all

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

# Set up logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(message)s",
)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    logger.info("Starting Property Tax Agent application")
    await init_db()
    logger.info("Database initialized")

    # Seed initial data for Phase 2 (Transaction Processing & Learning)
    async with AsyncSessionLocal() as db:
        try:
            results = await seed_all(db)
            logger.info(f"Seed data loaded: {results}")
        except Exception as e:
            logger.warning(f"Seed data may already exist: {e}")

    yield

    # Shutdown
    logger.info("Shutting down Property Tax Agent application")


# Create FastAPI app
app = FastAPI(
    title="NZ Property Tax Document Review",
    description="Document review system for NZ rental property tax returns",
    version="1.0.0",
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(api_router)
app.include_router(web_router)
app.include_router(transaction_router)
app.include_router(transaction_web_router)
app.include_router(categorization_router)
app.include_router(categorization_web_router)
app.include_router(skill_learning_router)
app.include_router(workings_router)

# Mount static files if needed
# app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "property-tax-agent",
        "model": settings.CLAUDE_MODEL,
        "debug": settings.DEBUG,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
