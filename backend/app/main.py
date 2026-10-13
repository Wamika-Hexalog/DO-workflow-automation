"""FastAPI application factory.

    uvicorn app.main:create_app --factory --reload
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .api import router
from .config import Settings
from .db import Profile, make_sessionmaker
from .folder import FolderError, load_folder
from .letters import RENDERERS
from .seed import ensure_profile, seed_demo_data


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    folder = load_folder(settings.folder_path)
    unknown = [d.key for d in folder.documents if d.source == "generate" and d.key not in RENDERERS]
    if unknown:
        raise FolderError(
            f"{settings.folder_path}: no letter is written for {', '.join(unknown)}. "
            f"Generated documents are: {', '.join(RENDERERS)}."
        )
    sessionmaker = make_sessionmaker(settings.database_url)

    with sessionmaker() as session:
        if settings.seed_demo_data and session.scalar(select(func.count()).select_from(Profile)) == 0:
            seed_demo_data(session, folder, settings)
        ensure_profile(session, folder)

    app = FastAPI(title="DO Workflow API", version="0.2.0")
    app.state.settings = settings
    app.state.folder = folder
    app.state.sessionmaker = sessionmaker
    app.include_router(router)

    # The built UI (npm run build), so the demo runs as a single process.
    if settings.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="web")
    return app
