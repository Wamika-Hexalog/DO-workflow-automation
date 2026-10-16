"""Sign-in: the board's API is closed until a person signs in."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth import ensure_user
from app.main import DEMO_EMAIL, DEMO_PASSWORD, DEMO_USER, create_app
from conftest import RecordingInngest, settings_for


def _app(tmp_path, private_dir, seed: bool = False):
    app = create_app(settings_for(tmp_path, private_dir, seed=seed), RecordingInngest())
    with app.state.rt.sessionmaker() as session:
        ensure_user(session, "ops", "right-password")
    return app


def test_the_api_opens_only_for_a_signed_in_session_and_closes_on_sign_out(tmp_path, private_dir):
    # Protects: the API needs a session; a wrong password gets none; the cookie can't be read by script or sent
    # cross-site; sign-out ends it. Fails if the API router drops its login check or sign-out leaves the session.
    with TestClient(_app(tmp_path, private_dir)) as api:
        assert api.get("/api/shipments").status_code == 401
        assert api.get("/api/webhooks/microsoft-graph?validationToken=t").text == "t"
        assert api.post("/api/auth/login", json={"username": "ops", "password": "wrong"}).status_code == 401
        assert api.post("/api/auth/login", json={"username": "nobody", "password": "wrong"}).status_code == 401

        signed_in = api.post("/api/auth/login", json={"username": " OPS ", "password": "right-password"})
        assert signed_in.json() == {"username": "ops"}
        cookie = signed_in.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert api.get("/api/shipments").status_code == 200
        assert api.get("/api/auth/me").json() == {"username": "ops"}

        assert api.post("/api/auth/logout").status_code == 204
        assert api.get("/api/shipments").status_code == 401


def test_a_stolen_cookie_is_useless_after_sign_out(tmp_path, private_dir):
    # Protects: sign-out ends the session on the server, not only in the browser. Fails if logout only clears the cookie.
    with TestClient(_app(tmp_path, private_dir)) as api:
        token = api.post("/api/auth/login", json={"username": "ops", "password": "right-password"}).cookies["do_session"]
        api.post("/api/auth/logout")
        api.cookies.set("do_session", token)
        assert api.get("/api/shipments").status_code == 401


def test_five_wrong_passwords_lock_the_user_out_even_with_the_right_one(tmp_path, private_dir):
    # Protects: password guessing is cut off. Fails if failed sign-ins are not counted.
    app = _app(tmp_path, private_dir)
    with app.state.rt.sessionmaker() as session:
        ensure_user(session, "locked", "right-password")
    with TestClient(app) as api:
        for _ in range(5):
            assert api.post("/api/auth/login", json={"username": "locked", "password": "guess"}).status_code == 401
        assert api.post("/api/auth/login", json={"username": "locked", "password": "right-password"}).status_code == 429


def test_the_demo_user_signs_in_with_the_username_or_the_email(tmp_path, private_dir):
    # Protects: the demo user exists when the demo data is seeded, and either name signs it in. Fails if
    # create_app stops adding it, or the login looks up only the username.
    with TestClient(_app(tmp_path, private_dir, seed=True)) as api:
        for name in (DEMO_USER, DEMO_EMAIL.upper()):
            signed_in = api.post("/api/auth/login", json={"username": name, "password": DEMO_PASSWORD})
            assert signed_in.json() == {"username": DEMO_USER}
