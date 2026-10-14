"""The Inngest workflows, run in process by the test engine from conftest."""
from __future__ import annotations

from pathlib import Path

import inngest
import pytest
from fastapi.testclient import TestClient

from app import api as api_module
from app.main import create_app
from app.workflows import browser
from app.workflows.events import EventNames
from conftest import (
    HBL,
    MBL,
    Engine,
    RecordingInngest,
    attach,
    attach_everything,
    build,
    make_pdf,
    new_shipment,
    settings_for,
    task,
)

HBL_2 = "MSEC260803047403"


def events_of(api: TestClient, shipment: dict) -> list[str]:
    return [e["detail"] for e in api.get(f"/api/shipments/{shipment['id']}/events").json()]


def approve(api: TestClient, shipment: dict, by: str = "Ravi") -> dict:
    response = api.post(f"/api/shipments/{shipment['id']}/tasks/odex_do_request/complete", json={"by": by})
    assert response.status_code == 202, response.text
    return response.json()


def test_changes_are_announced_to_the_workflow_engine(api, engine):
    s = new_shipment(api)
    attach(api, s, "mbl", make_pdf(f"MASTER BILL {MBL}"))
    api.patch(f"/api/shipments/{s['id']}/values", json={"values": {"voyage": "0FBI6W1MA"}})
    api.post(f"/api/shipments/{s['id']}/slots/mbl/check")
    assert engine.sent_names() == [
        EventNames.DOCUMENT_RECEIVED,
        EventNames.SHIPMENT_UPDATED,
        EventNames.SHIPMENT_UPDATED,
    ]


def test_the_review_task_follows_the_documents(api, engine):
    s = attach(api, new_shipment(api), "mbl", make_pdf(f"MASTER BILL {MBL}"))
    engine.run()
    review = task(api.get(f"/api/shipments/{s['id']}").json(), "folder_review")
    assert review["status"] == "pending" and "missing" in review["note"]

    attach_everything(api, s)
    engine.run()
    review = task(api.get(f"/api/shipments/{s['id']}").json(), "folder_review")
    assert review["status"] == "awaiting_approval"
    assert review["note"].startswith("All 12 documents are in place")
    assert any(fn == "do-folder-verify" and name == EventNames.DOCUMENT_RECEIVED for fn, name, _ in engine.runs)
    assert "All 12 documents are in place. Review them, then build the folder." in events_of(api, s)


def test_a_rebuilt_folder_needs_review_again(api, engine):
    s = build(api, engine, attach_everything(api, new_shipment(api)))
    assert task(s, "folder_review")["status"] == "completed"

    api.patch(f"/api/shipments/{s['id']}/values", json={"values": {"voyage": "0FBI6W1MA"}})
    engine.run()
    s = api.get(f"/api/shipments/{s['id']}").json()
    assert s["folder"]["stale"] is True
    assert task(s, "folder_review")["status"] == "awaiting_approval"
    assert "The DO folder changed after it was built, so it needs review again." in events_of(api, s)


def test_nothing_starts_if_the_engine_is_down(api, client):
    s = attach_everything(api, new_shipment(api))  # notices fail quietly; the uploads still stand
    client.down = True
    response = api.post(f"/api/shipments/{s['id']}/build", json={"confirmed": True})
    assert response.status_code == 503 and "make dev" in response.json()["detail"]
    s = api.get(f"/api/shipments/{s['id']}").json()
    assert s["building"] is False and s["docs_ok"] == 12
    assert task(s, "folder_review")["status"] != "completed"


def test_a_build_already_under_way_is_not_started_twice(api):
    s = attach_everything(api, new_shipment(api))
    assert api.post(f"/api/shipments/{s['id']}/build", json={"confirmed": True}).status_code == 202
    again = api.post(f"/api/shipments/{s['id']}/build", json={"confirmed": True})
    assert again.status_code == 409 and "already being built" in again.json()["detail"]


def test_a_failed_build_goes_back_to_ops(api, engine, private_dir):
    s = attach_everything(api, new_shipment(api))
    assert api.post(f"/api/shipments/{s['id']}/build", json={"confirmed": True}).status_code == 202
    stamp = private_dir / "stamps" / "hexalog.png"
    saved = stamp.read_bytes()
    stamp.unlink()  # gone between the confirmation and the build
    engine.run()
    s = api.get(f"/api/shipments/{s['id']}").json()
    review = task(s, "folder_review")
    # The build checks again before it starts, so the missing stamp is named as such.
    assert review["status"] == "failed"
    assert review["note"].startswith("3 documents need attention first: MBL (Master Bill of Lading)")
    assert s["building"] is False and s["folder"] is None

    stamp.write_bytes(saved)
    s = build(api, engine, s)
    assert s["status"] == "folder_ready"


def test_building_opens_the_do_request_for_ops(api, engine):
    s = new_shipment(api)
    refused = api.post(f"/api/shipments/{s['id']}/tasks/odex_do_request/complete", json={})
    assert refused.status_code == 409 and "build the DO folder first" in refused.json()["detail"]

    s = build(api, engine, attach_everything(api, s))
    odex = task(s, "odex_do_request")
    assert odex["status"] == "awaiting_approval"
    assert "raise the do request" in odex["note"].lower() and HBL in odex["note"]


def test_a_do_request_raised_by_hand_is_recorded(api, engine):
    s = build(api, engine, attach_everything(api, new_shipment(api)))
    s = approve(api, s, by="Ravi")
    engine.run()
    s = api.get(f"/api/shipments/{s['id']}").json()
    assert s["status"] == "do_requested" and s["requested_on"]
    assert task(s, "odex_do_request")["status"] == "completed"
    assert f"DO request raised on ODeX by hand for HBL {HBL}, confirmed by Ravi." in events_of(api, s)
    assert EventNames.REQUEST_SUBMITTED in engine.delivered


def two_hbl_shipment(api: TestClient) -> dict:
    s = attach_everything(api, new_shipment(api, hbl_nos=f"{HBL}\n{HBL_2}"))
    s = attach(api, s, f"hbl:{HBL_2}", make_pdf(f"HOUSE BILL MSEC-260803047 -403 B/L {MBL}"))
    return attach(api, s, f"security_odex_receipt:{HBL_2}", make_pdf(f"SECURITY DEPOSIT SLIP B/L No. {HBL_2}"))


@pytest.fixture()
def automated(tmp_path: Path, private_dir: Path):
    """The app with an ODeX SOP in place, and a stand-in for the browser run."""
    sop = tmp_path / "sop_odex_do_request_new.yaml"
    sop.write_text("metadata: {name: ODeX test}\nsteps: []\n", encoding="utf-8")
    client = RecordingInngest()
    app = create_app(settings_for(tmp_path, private_dir, odex_sop_path=sop), client)
    with TestClient(app) as test_client:
        yield test_client, Engine(app, client)


def test_the_browser_agent_raises_one_request_per_house_bill(automated, monkeypatch):
    api, engine = automated
    calls = []

    async def run_sop(rt, sop_path, params, job_label, **kwargs):
        calls.append(params)
        assert Path(params["folder_pdf_path"]).is_file()
        return {"status": "completed", "extraction_data": {"odex_request_ref": f"DO-{params['hbl_no'][-3:]}"}, "run_id": "r1"}

    monkeypatch.setattr(browser, "run_sop", run_sop)
    s = build(api, engine, two_hbl_shipment(api))
    approve(api, s)
    engine.run()

    assert [c["hbl_no"] for c in calls] == [HBL, HBL_2]
    assert {c["mbl_no"] for c in calls} == {MBL}
    assert calls[0]["folder_pdf_path"] == calls[1]["folder_pdf_path"]  # one PDF per MBL
    # The rest of what the SOP fills in comes from the job and the fixed details.
    assert (calls[0]["liner"], calls[0]["do_type"], calls[0]["odex_url"]) == ("CMA CGM", "E-DO", "http://127.0.0.1:8100/odex")
    s = api.get(f"/api/shipments/{s['id']}").json()
    assert s["status"] == "do_requested"
    log = events_of(api, s)
    assert f"DO request raised on ODeX for HBL {HBL}, reference DO-251." in log
    assert f"DO request raised on ODeX for HBL {HBL_2}, reference DO-403." in log

    # A replayed approval never raises the same request twice.
    client = engine.client
    client.sent.append(
        inngest.Event(
            name=EventNames.TASK_COMPLETED,
            data={"shipment_id": s["id"], "task_key": "odex_do_request", "completed_by": "Ravi"},
        )
    )
    engine.run()
    assert len(calls) == 2


def test_a_failed_odex_run_tells_ops_what_to_check(automated, monkeypatch):
    api, engine = automated
    calls = []

    async def run_sop(rt, sop_path, params, job_label, **kwargs):
        calls.append(params["hbl_no"])
        if params["hbl_no"] == HBL_2 and calls.count(HBL_2) == 1:
            raise inngest.NonRetriableError(f"SOP did not complete for {job_label}: upload button not found")
        return {"status": "completed", "extraction_data": {"odex_request_ref": "DO-1"}, "run_id": "r1"}

    monkeypatch.setattr(browser, "run_sop", run_sop)
    s = build(api, engine, two_hbl_shipment(api))
    approve(api, s)
    engine.run()

    s = api.get(f"/api/shipments/{s['id']}").json()
    odex = task(s, "odex_do_request")
    assert odex["status"] == "failed"
    assert "upload button not found" in odex["note"]
    assert f"Check ODeX for HBL {HBL_2} before approving again" in odex["note"]
    assert s["status"] == "folder_ready"

    # Ops checked ODeX and try again: the first house bill is not raised twice.
    approve(api, s)
    engine.run()
    assert calls == [HBL, HBL_2, HBL_2]
    assert api.get(f"/api/shipments/{s['id']}").json()["status"] == "do_requested"


def test_notices_never_hold_up_a_request(api, client):
    client.down = True
    s = new_shipment(api)
    response = api.patch(f"/api/shipments/{s['id']}/values", json={"values": {"voyage": "X1"}})
    assert response.status_code == 200
    api_module.flush_notices()


def test_an_sop_without_an_end_step_is_refused_before_a_browser_opens(app, tmp_path):
    # Without `action: end` the agent repeats the last step until its iterations
    # run out: on ODeX that last step could be the submit.
    import asyncio

    sop = tmp_path / "no_end.yaml"
    sop.write_text(
        "metadata: {name: No end}\nsteps:\n  - {action: click, method: playwright, selector: '#submit'}\n",
        encoding="utf-8",
    )
    with pytest.raises(inngest.NonRetriableError, match="must finish with an `action: end` step"):
        asyncio.run(browser.run_sop(app.state.rt, str(sop), {}, "test"))


def test_a_failed_run_says_what_the_page_said():
    # The agent's own summary only names the step; the portal's words are in the step's result.
    result = {
        "error": "Step failed 3 consecutive times: Read the request number ODeX gave.",
        "messages": [
            {"role": "user", "result": {"status": "success", "action_type": "click"}},
            {"role": "user", "result": {"status": "error", "message": "Page.evaluate: Error: ODeX turned the DO request down: duplicate\n    at eval"}},
        ],
    }
    assert browser.step_error(result) == "ODeX turned the DO request down: duplicate"
    assert browser.step_error({"messages": []}) is None


def test_a_scripted_sop_needs_no_model_key(monkeypatch):
    import os

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    scripted = [{"action": "goto", "method": "playwright"}, {"action": "end"}]
    with browser._model_key_for(scripted):
        assert os.environ["GOOGLE_API_KEY"]
    assert "GOOGLE_API_KEY" not in os.environ
    # A step that asks the model needs a real key.
    with browser._model_key_for([{"action": "act", "method": "stagehand"}, {"action": "end"}]):
        assert "GOOGLE_API_KEY" not in os.environ
