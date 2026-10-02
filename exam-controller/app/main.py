from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.api.health import router as health_router
from app.api.agents import router as agents_router
from app.api.migration import router as migration_router
from app.database import initialize_database
from app.services import agents

logger = logging.getLogger(__name__)
APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="EXAM CONTROLLER",
    description="Local-LAN device management API for an authorized school lab.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(health_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(migration_router, prefix="/api")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.get("/", include_in_schema=False)
def dashboard(request: Request):
    try:
        rows = agents.get_all()
        error = None
    except Exception:
        logger.exception("Could not load dashboard agent list")
        rows = []
        error = "Agent data is temporarily unavailable. Retrying automatically."
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"agents": rows, "error": error},
    )
