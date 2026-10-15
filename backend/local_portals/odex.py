"""The local ODeX: its sessions and its files. Its pages are the team's ODeX guides'
screenshots with live buttons over them, in odex_screens.py.

Requests then wait for CMA CGM, which the control panel plays.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from .store import Store

router = APIRouter(prefix="/odex")

COOKIE = "odex_session"
SESSION_HOURS = 12  # how long a sign-in lasts


def store_of(request: Request) -> Store:
    return request.app.state.store


def signed_in(session: Any) -> bool:
    """A session through both of the login's steps (the password, then the company)
    that hasn't run out."""
    return (
        isinstance(session, dict)
        and bool(session.get("company"))
        and session["at"] > (datetime.now() - timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
    )


def current_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with store_of(request).read() as data:
        session = data["sessions"].get(token)
    return session["user"] if signed_in(session) else None


def to_login(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/odex/login?next={quote(request.url.path)}", status_code=303)


@router.get("")
def root(request: Request) -> Response:
    return RedirectResponse("/odex/home" if current_user(request) else "/odex/login", status_code=303)


@router.get("/logout")
def logout(request: Request) -> Response:
    token = request.cookies.get(COOKIE)
    if token:
        with store_of(request).change() as data:
            data["sessions"].pop(token, None)
    response = RedirectResponse("/odex/login", status_code=303)
    response.delete_cookie(COOKIE, path="/odex")
    return response


@router.get("/files/{file_id}")
def download(file_id: str, request: Request) -> Response:
    if current_user(request) is None:
        return to_login(request)
    found = store_of(request).file(file_id)
    if found is None:
        return Response("No such file.", status_code=404)
    name, path = found
    # As an attachment, so a browser saves it instead of opening its PDF viewer.
    return FileResponse(path, media_type="application/pdf", filename=name)
