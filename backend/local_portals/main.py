"""The local portals app: ODeX, CMA CGM's RTGS page and the control panel on one
port, with a home page linking them.

    make portals    # uvicorn local_portals.main:create_app --factory --port 8100
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from . import cma, cma_rtgs, control, odex, pages, settings
from .pages import esc
from .store import Store


def create_app(data_dir: Path | None = None, *, autoplay: bool = True) -> FastAPI:
    """``autoplay=False`` leaves out the thread that answers requests by itself:
    tests answer them by calling cma directly."""
    store = Store(data_dir or settings.data_dir())
    player = cma.Autoplay(store)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if autoplay:
            player.start()
        yield
        player.stop()

    app = FastAPI(title="Local portals (demo)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.autoplay = player
    app.include_router(odex.router)
    app.include_router(cma_rtgs.router)
    app.include_router(control.router)

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        username, password = settings.odex_login()
        login = (
            f"Log in with <code>{esc(username)}</code> / <code>{esc(password)}</code>."
            if settings.default_login()
            else "Log in with LOCAL_ODEX_USERNAME and LOCAL_ODEX_PASSWORD from backend/.env."
        )
        body = f"""<h1>Local portals</h1>
<p class="path">Stand-ins for the websites of the CMA CGM DO process, so it can be shown, and its SOPs written,
without touching the real sites. Everything stays on this machine.</p>
<div class="card"><h2 style="margin-top:0"><a href="/odex">ODeX</a></h2>
<p>Request the DO invoice, register the security deposit and raise the DO request, then download the deposit
slip and the eDO. {login}</p></div>
<div class="card"><h2 style="margin-top:0"><a href="/cma-rtgs">CMA CGM RTGS payments</a></h2>
<p>Enter the DO invoice and security deposit payments against the B/L, so CMA CGM emails the receipts.</p></div>
<div class="card"><h2 style="margin-top:0"><a href="/control">Control panel</a></h2>
<p>Plays CMA CGM: verifies invoices, emails receipts, approves deposits and issues eDOs, by a click or by
autoplay. It also shows the emails CMA CGM would send.</p></div>
<p class="hint">The DO workflow app opens these by default (DO_ODEX_URL and DO_CMA_RTGS_URL), and its browser
agent may open nothing else unless DO_BROWSER_ALLOWED_HOSTS says so.</p>"""
        return pages.page("Home", body, site="home")

    return app
