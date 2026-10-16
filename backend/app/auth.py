"""Sign-in for the team: a password checked against the users table, then a session cookie
the browser can't read from script (HttpOnly) or send from another site (SameSite=Strict)."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from .db import LoginSession, User, utcnow
from .service import aware

COOKIE = "do_session"
SESSION_TTL = timedelta(hours=12)
MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=5)
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}

router = APIRouter(prefix="/api/auth")

# shortcut: failed sign-ins are counted in memory, so a restart clears them; move to the
# database if the app ever runs as more than one process.
_failures: dict[str, tuple[int, datetime | None]] = {}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _password_ok(password: str, stored: str) -> bool:
    _, salt, digest = stored.split("$")
    return hmac.compare_digest(hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT), bytes.fromhex(digest))


# Checked for an unknown username, so the time taken does not say which usernames exist.
_NO_USER_HASH = hash_password(secrets.token_hex(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ensure_user(session: Session, username: str, password: str, email: str | None = None) -> None:
    """Adds the user if there is none by that name. An existing password is never reset."""
    if session.get(User, username) is None:
        session.add(User(username=username, email=email, password_hash=hash_password(password)))
        session.commit()


def current_user(request: Request) -> str:
    token = request.cookies.get(COOKIE)
    if token:
        with request.app.state.rt.sessionmaker() as session:
            row = session.get(LoginSession, _token_hash(token))
            if row is not None and aware(row.expires_at) > utcnow():
                return row.username
    raise HTTPException(status_code=401, detail="Sign in to continue.")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    username: str


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response) -> UserOut:
    login_name = body.username.strip().lower()  # the username or the email
    count, until = _failures.get(login_name, (0, None))
    if until is not None and until > utcnow():
        raise HTTPException(status_code=429, detail="Too many failed sign-ins. Wait 5 minutes, then try again.")
    with request.app.state.rt.sessionmaker() as session:
        user = session.scalar(select(User).where(or_(User.username == login_name, User.email == login_name)))
        ok = _password_ok(body.password, user.password_hash if user else _NO_USER_HASH) and user is not None
        if not ok:
            count += 1
            _failures[login_name] = (0, utcnow() + LOCKOUT) if count >= MAX_FAILURES else (count, None)
            raise HTTPException(status_code=401, detail="Wrong username or password.")
        _failures.pop(login_name, None)
        username = user.username
        # A new token on every sign-in, so a token planted before it is worth nothing.
        token = secrets.token_urlsafe(32)
        session.execute(delete(LoginSession).where(LoginSession.expires_at < utcnow()))
        session.add(LoginSession(token_hash=_token_hash(token), username=username, expires_at=utcnow() + SESSION_TTL))
        session.commit()
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return UserOut(username=username)


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    token = request.cookies.get(COOKIE)
    if token:
        with request.app.state.rt.sessionmaker() as session:
            session.execute(delete(LoginSession).where(LoginSession.token_hash == _token_hash(token)))
            session.commit()
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/me")
def me(request: Request) -> UserOut:
    return UserOut(username=current_user(request))
