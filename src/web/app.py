"""
Project Syndicate — Web Frontend
Command Center for an AI Trading Ecosystem
"""

__version__ = "2.0.0"

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Only create engine if not already set (tests inject their own)
    if getattr(app.state, "_db_initialized", False):
        yield
        return

    from dotenv import load_dotenv
    load_dotenv()

    db_url = os.getenv("DATABASE_URL", "postgresql://postgres@localhost:5432/syndicate")
    engine = create_engine(db_url, pool_pre_ping=True)
    app.state.db_session_factory = sessionmaker(bind=engine)
    app.state.engine = engine
    app.state._db_initialized = True

    yield

    engine.dispose()


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title="Project Syndicate",
        description="Command Center for an AI Trading Ecosystem",
        version=__version__,
        lifespan=lifespan,
    )

    # Static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Templates
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Jinja2 filter for UTC ISO timestamps
    from src.web.dependencies import format_utc_timestamp
    app.state.templates.env.filters["utc_iso"] = format_utc_timestamp

    # Routes
    from src.web.routes.pages import router as pages_router
    from src.web.routes.api_agora import router as api_agora_router
    from src.web.routes.api_leaderboard import router as api_leaderboard_router
    from src.web.routes.api_library import router as api_library_router
    from src.web.routes.api_agents import router as api_agents_router
    from src.web.routes.api_system import router as api_system_router
    from src.web.routes.api_personality import router as api_personality_router
    from src.web.routes.api_dynasty import router as api_dynasty_router
    from src.web.routes.api_sse import router as api_sse_router
    from src.web.routes.api_governance import router as api_governance_router

    app.include_router(pages_router)
    app.include_router(api_agora_router, prefix="/api/agora")
    app.include_router(api_leaderboard_router, prefix="/api/leaderboard")
    app.include_router(api_library_router, prefix="/api/library")
    app.include_router(api_agents_router, prefix="/api/agents")
    app.include_router(api_system_router, prefix="/api/system")
    app.include_router(api_personality_router, prefix="/api/personality")
    app.include_router(api_dynasty_router, prefix="/api/dynasties")
    app.include_router(api_sse_router, prefix="/api/events")
    app.include_router(api_governance_router)

    # Admin catch-all redirect (Phase 6 adds auth)
    @app.get("/admin/{path:path}")
    async def admin_redirect(path: str):
        return RedirectResponse(url=f"/{path}", status_code=302)

    return app


app = create_app()
