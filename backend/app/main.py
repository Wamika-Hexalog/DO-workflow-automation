"""FastAPI application factory.

    uvicorn app.main:create_app --factory --reload

The workflows are served at /api/inngest. Locally the Inngest dev server calls
them there (`make dev` starts both).
"""
from __future__ import annotations

import inngest
import inngest.fast_api
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .api import router
from .config import Settings
from .db import Profile, make_sessionmaker
from .folder import FolderError, load_folder
from .letters import RENDERERS
from .runtime import Runtime
from .seed import ensure_profile, seed_demo_data
from .workflows import create_functions
from .workflows.client import make_client


def create_app(settings: Settings | None = None, inngest_client: inngest.Inngest | None = None) -> FastAPI:
    """``inngest_client`` lets tests pass a mocked client that records events."""
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

    rt = Runtime(settings, folder, sessionmaker, inngest_client or make_client(settings))
    functions = create_functions(rt)

    app = FastAPI(title="DO Workflow API", version="0.3.0")
    app.state.rt = rt
    app.state.functions = {fn.local_id: fn for fn in functions}
    app.include_router(router)
    # Before the UI mount below, which would otherwise answer every path.
    inngest.fast_api.serve(app, rt.inngest, functions, serve_origin=settings.inngest_serve_origin)

    # The built UI (npm run build), so the demo runs as a single process.
    if settings.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="web")
    return app
