"""FastAPI application factory.

    uvicorn app.main:create_app --factory --reload

The workflows are served at /api/inngest. Locally the Inngest dev server calls
them there (`make dev` starts both).
"""
from __future__ import annotations

import base64
import hmac
import logging
from urllib.parse import urlparse

import inngest
import inngest.fast_api
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .api import router
from .auth import ensure_user, router as auth_router
from .connectors import _settings as connector_settings
from .config import Settings
from .db import Profile, make_sessionmaker
from .folder import FolderError, load_folder
from .letters import RENDERERS
from .runtime import Runtime
from .seed import ensure_profile, seed_demo_data
from .webhooks import lifespan, router as webhooks_router
from .workflows import create_functions
from .workflows.client import make_client


GRAPH_WEBHOOK_PATH = "/api/webhooks/microsoft-graph"
# The demo login. Made only with the demo data (DO_SEED_DEMO_DATA), never for real use.
DEMO_USER, DEMO_EMAIL, DEMO_PASSWORD = "customs-ops", "customs.ops@hexalog.in", "hexalog-demo"
logger = logging.getLogger(__name__)


def _logged_in(header: str, logins: tuple[tuple[str, str], ...]) -> bool:
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        user, _, password = base64.b64decode(token).decode().partition(":")
    except ValueError:
        return False
    # Check every login so the time taken does not say which one matched.
    matches = [
        hmac.compare_digest(user.encode(), u.encode()) & hmac.compare_digest(password.encode(), p.encode())
        for u, p in logins
    ]
    return any(matches)


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
    connector_settings.use(settings)
    sessionmaker = make_sessionmaker(settings.database_url)

    with sessionmaker() as session:
        if settings.seed_demo_data and session.scalar(select(func.count()).select_from(Profile)) == 0:
            seed_demo_data(session, folder, settings)
        ensure_profile(session, folder)
        if settings.seed_demo_data:
            ensure_user(session, DEMO_USER, DEMO_PASSWORD, DEMO_EMAIL)

    rt = Runtime(settings, folder, sessionmaker, inngest_client or make_client(settings))
    functions = create_functions(rt)

    app = FastAPI(title="DO Workflow API", version="0.3.0", lifespan=lifespan)
    app.state.rt = rt
    app.state.functions = {fn.local_id: fn for fn in functions}
    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(webhooks_router)

    # The public tunnel (WEBHOOK_BASE_URL). Graph reaches the webhook there, and the team can open
    # the app there behind a login. Nothing else is open: the tunnel asks for this login before the app's own sign-in, and
    # /api/inngest does not check signatures in dev mode, so that path is never served on the tunnel.
    # Local requests (localhost) are unchanged. Do not run ngrok with --host-header=rewrite: this
    # reads the Host header, and the guard does nothing once it is localhost.
    public_host = (urlparse(settings.webhook_base_url).hostname or "").lower()
    logins = settings.basic_auth_logins
    if public_host and not logins:
        logger.warning("WEBHOOK_BASE_URL is set but BASIC_AUTH_USERS is not: the tunnel serves only the webhook")

    @app.middleware("http")
    async def public_host_guard(request: Request, call_next):  # noqa: ANN001, ANN202
        if not public_host or request.headers.get("host", "").split(":")[0].lower() != public_host:
            return await call_next(request)
        path = request.url.path
        if path == GRAPH_WEBHOOK_PATH:
            return await call_next(request)  # Graph can't send a login; the webhook checks clientState
        if path.startswith("/api/inngest") or not logins:
            return Response(status_code=404)
        if not _logged_in(request.headers.get("authorization", ""), logins):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="DO Workflow"'})
        return await call_next(request)

    # Before the UI mount below, which would otherwise answer every path.
    inngest.fast_api.serve(app, rt.inngest, functions, serve_origin=settings.inngest_serve_origin)

    # The built UI (npm run build), so the demo runs as a single process.
    if settings.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="web")
    return app
