"""FastAPI application factory.

    uvicorn app.main:create_app --factory --reload
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .api import router
from .config import Settings
from .db import Client, make_sessionmaker
from .do_set import load_do_set
from .seed import seed_demo_data


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    do_set = load_do_set(settings.do_set_path)
    sessionmaker = make_sessionmaker(settings.database_url)

    if settings.seed_demo_data:
        with sessionmaker() as session:
            if session.scalar(select(func.count()).select_from(Client)) == 0:
                seed_demo_data(session, do_set, settings.job_prefix)

    app = FastAPI(title="DO Workflow API", version="0.1.0")
    app.state.settings = settings
    app.state.do_set = do_set
    app.state.sessionmaker = sessionmaker
    app.include_router(router)

    # The built UI (npm run build), so the demo runs as a single process.
    if settings.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="web")
    return app
