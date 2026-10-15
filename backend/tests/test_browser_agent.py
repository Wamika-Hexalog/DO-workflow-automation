"""python-brain's browser agent, run for real against a mock ODeX page.

It opens Chromium, so it is slow and opt-in: ``make test-browser`` or
``pytest -m browser``. Set DO_BROWSER_HEADLESS=0 to watch it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import HBL, Engine, RecordingInngest, attach_everything, build, new_shipment, settings_for

pytestmark = pytest.mark.browser

FIXTURES = Path(__file__).parent / "fixtures" / "mock_odex"


@pytest.fixture()
def portal_sop(tmp_path: Path) -> Path:
    page = (FIXTURES / "new_do_request.html").resolve()
    sop = tmp_path / "sop_mock_odex_do_request.yaml"
    sop.write_text(
        (FIXTURES / "sop_mock_odex_do_request.yaml").read_text(encoding="utf-8").replace("__PORTAL_URL__", page.as_uri()),
        encoding="utf-8",
    )
    return sop


def test_the_agent_raises_the_do_request_on_the_portal(tmp_path, private_dir, portal_sop, monkeypatch):
    # No step here asks the model anything, but the agent won't start without a key.
    monkeypatch.setenv("GOOGLE_API_KEY", os.getenv("GOOGLE_API_KEY") or "not-used-by-playwright-steps")
    headless = os.getenv("DO_BROWSER_HEADLESS", "1") != "0"
    client = RecordingInngest()
    app = create_app(
        settings_for(tmp_path, private_dir, odex_sop_path=portal_sop, browser_headless=headless), client
    )
    with TestClient(app) as api:
        engine = Engine(app, client)
        s = build(api, engine, attach_everything(api, new_shipment(api)))
        assert api.post(f"/api/shipments/{s['id']}/tasks/odex_do_request/complete", json={"by": "Ravi"}).status_code == 202
        engine.run()

        s = api.get(f"/api/shipments/{s['id']}").json()
        assert s["status"] == "do_requested", [t["note"] for t in s["tasks"]]
        log = [e["detail"] for e in api.get(f"/api/shipments/{s['id']}/events").json()]
        assert f"DO request raised on ODeX for HBL {HBL}, reference DOREQ-047251." in log

        runs = api.get(f"/api/shipments/{s['id']}/runs").json()
        assert len(runs) == 1
        run = runs[0]
        assert run["status"] == "completed" and run["task_key"] == "odex_do_request"
        assert run["sop_name"] == "Mock ODeX - New DO Request"
        assert run["extractions"][0]["data"]["odex_request_ref"] == "DOREQ-047251"
        # The portal saw the folder PDF itself, not just a file name.
        assert f"ALL {HBL[-4:]}.pdf" in run["extractions"][0]["data"]["odex_confirmation"]
