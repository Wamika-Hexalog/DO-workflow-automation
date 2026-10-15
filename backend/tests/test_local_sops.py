"""The whole CMA CGM process on the local portals, with python-brain's browser agent
in a real Chromium: every SOP in sops/local, filled from the database, and the
documents the portals hand out going into the DO folder and passing its checks.

It opens Chromium, so it is slow and opt-in, like test_browser_agent:
``make test-browser`` or ``pytest -m browser``. DO_BROWSER_HEADLESS=0 shows it.
"""
from __future__ import annotations

import asyncio
import io
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import inngest
import pytest
import uvicorn
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import portal_params
from app.config import LOCAL_ODEX_LOGIN
from app.main import create_app
from app.portal_params import (
    DEPOSIT,
    DEPOSIT_STATUS,
    DO_REQUEST,
    DO_REQUEST_STATUS,
    INVOICE_FETCH,
    REQUEST_INVOICE,
    RTGS_DEPOSIT,
    RTGS_SHIPMENT,
)
from app.workflows import browser
from conftest import HBL, MBL, Engine, RecordingInngest, attach, build, make_pdf, new_shipment, settings_for, task
from local_portals import cma, mail
from local_portals.main import create_app as create_portals

pytestmark = pytest.mark.browser


@pytest.fixture()
def portals(tmp_path, monkeypatch):
    """The local portals, served on a free port for the browser to open."""
    for name, value in LOCAL_ODEX_LOGIN.items():
        monkeypatch.setenv(name, value)
    app = create_portals(tmp_path / "portals", autoplay=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert time.monotonic() < deadline, "the local portals didn't start"
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}", store=app.state.store)
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture()
def do_app(tmp_path, private_dir, portals):
    """The DO app pointed at the local portals, with the local SOP for the DO request."""
    settings = settings_for(
        tmp_path,
        private_dir,
        odex_sop_path=portal_params.STEPS[DO_REQUEST].sop,
        browser_headless=os.getenv("DO_BROWSER_HEADLESS", "1") != "0",
        odex_url=f"{portals.url}/odex",
        cma_rtgs_url=f"{portals.url}/cma-rtgs",
    )
    client = RecordingInngest()
    app = create_app(settings, client)
    with TestClient(app) as api:
        yield api, Engine(app, client)


@pytest.fixture()
def process_app(tmp_path, private_dir, portals, monkeypatch):
    """The app with every portal step on (DO_AUTOMATE_PORTALS), served on a free port,
    so the local CMA CGM and Finance mail it through its Graph webhook, as in the demo."""
    settings = settings_for(
        tmp_path,
        private_dir,
        odex_sop_path=portal_params.STEPS[DO_REQUEST].sop,
        browser_headless=os.getenv("DO_BROWSER_HEADLESS", "1") != "0",
        odex_url=f"{portals.url}/odex",
        cma_rtgs_url=f"{portals.url}/cma-rtgs",
        automate_portals=True,
    )
    client = RecordingInngest()
    app = create_app(settings, client)
    with TestClient(app) as api:
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            assert time.monotonic() < deadline, "the app didn't start"
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        # The local portals find the app's mailbox, and how it reads mail, as the demo does.
        for name, value in {
            "DO_PORTALS_MAIL_TO": f"http://127.0.0.1:{port}", "DO_STORAGE_DIR": str(settings.storage_dir),
            "MAIL_MARKER": settings.mail_marker, "INJECT_CODE_PREFIX": settings.inject_code_prefix,
            "GRAPH_CLIENT_STATE": settings.graph_client_state,
        }.items():
            monkeypatch.setenv(name, value)
        yield api, Engine(app, client)
        server.should_exit = True
        thread.join(timeout=10)


def run(api: TestClient, shipment: dict, step_key: str) -> list[dict]:
    """One step of the process for the job, the way the workflows run it: the
    step's SOP, its params from the database. What each run read back."""
    rt = api.app.state.rt
    step = portal_params.STEPS[step_key]
    readings = []
    for target, params in portal_params.params_job(rt, shipment["id"], step_key).items():
        outcome = asyncio.run(
            browser.run_sop(rt, str(step.sop), params, f"{step.title} for {target}", shipment_id=shipment["id"], task_key=step_key)
        )
        readings.append(outcome["extraction_data"])
    return readings


def answer(portals: SimpleNamespace, kind: str) -> None:
    """CMA CGM answers every request of a kind that waits for it."""
    with portals.store.read() as data:
        waiting = [item["id"] for item in data[kind] if item["status"] == cma.WAITING[kind]]
    assert waiting, f"nothing waits in {kind}"
    for item_id in waiting:
        cma.ANSWERS[kind](portals.store, item_id)


def emailed(portals: SimpleNamespace, name_starts: str) -> tuple[str, bytes]:
    """A file CMA CGM emailed, by the start of its name."""
    with portals.store.read() as data:
        file_ids = [file_id for mail in data["outbox"] for file_id in mail["attachments"]]
    for name, path in (portals.store.file(file_id) for file_id in file_ids):
        if name.startswith(name_starts):
            return name, path.read_bytes()
    raise AssertionError(f"CMA CGM emailed no {name_starts}")


def test_the_do_process_runs_on_the_local_portals(do_app, portals):
    api, engine = do_app
    fixed = {"do_mailbox": "do-desk@example.com", "payer_account_no": "123456789012"}
    assert api.patch("/api/profile", json={"values": fixed}).status_code == 200
    s = attach(api, new_shipment(api), "mbl", make_pdf(f"MASTER BILL {MBL}"))

    # IGM filed: request the DO invoice on ODeX, the MBL attached as the Seaway BL
    # (the team's ODeX guide, steps 1-5). The liner releases the invoice on ODeX.
    assert run(api, s, REQUEST_INVOICE)[0]["odex_invoice_request_ref"].startswith("BL")
    assert run(api, s, INVOICE_FETCH)[0]["invoice_status"] == "PENDING"  # not out yet: nothing downloaded
    answer(portals, "invoice_requests")
    with portals.store.read() as data:
        invoice = dict(data["invoice_requests"][0])
    assert invoice["attachments"][0]["name"] == f"MBL-{MBL}.pdf" and invoice["delivery_type"] == "Factory Destuff"

    # Guide, steps 6-10: EDO DETAILS, the request, its invoice. The invoice's PDF goes to
    # Finance; its number and amount go on the job.
    reading = run(api, s, INVOICE_FETCH)[0]
    assert (reading["invoice_no"], reading["invoice_amount"]) == (invoice["invoice_no"], f"{invoice['amount']:.2f}")
    assert MBL in PdfReader(reading["downloaded_file_path"]).pages[0].extract_text()
    saved = portal_params.save_readings(api.app.state.rt, s["id"], INVOICE_FETCH, reading)
    assert saved["do_invoice_no"] == invoice["invoice_no"]

    # What the Graph ingest will note from Finance's email: the UTRs and the date.
    paid = dict(
        payment_utr="DEMOUTR0001INV", deposit_utr="DEMOUTR0001DEP", payment_date=(date.today() - timedelta(days=1)).isoformat(),
    )
    s = api.patch(f"/api/shipments/{s['id']}/values", json={"values": paid}).json()

    # Finance paid: confirm each payment on CMA CGM's RTGS page. CMA CGM emails a receipt for each.
    assert run(api, s, RTGS_SHIPMENT)[0]["cma_rtgs_request_ref"].isdigit()
    assert run(api, s, RTGS_DEPOSIT)[0]["cma_rtgs_request_ref"].isdigit()
    with portals.store.read() as data:
        rows = [request["invoices"][0] for request in data["rtgs_requests"]]
    assert [(r["security_deposit"], r["invoice_no"], r["amount"]) for r in rows] == [
        (False, invoice["invoice_no"], invoice["amount"]),
        (True, "", 40000.0),
    ]
    answer(portals, "rtgs_requests")
    name, receipt = emailed(portals, "DO Receipt")
    s = attach(api, s, "do_receipt", receipt, name)
    name, receipt = emailed(portals, "Security Receipt")
    s = attach(api, s, "security_receipt", receipt, name)

    # The deposit, with Hexalog's NOC and the security receipt, then its slip once approved.
    assert run(api, s, DEPOSIT)[0]["odex_deposit_ref"].startswith("SD")
    assert run(api, s, DEPOSIT_STATUS)[0]["deposit_status"] == "Pending Approval"
    answer(portals, "deposits")
    status = run(api, s, DEPOSIT_STATUS)[0]
    assert status["deposit_status"] == "Deposit Approved"
    slip = Path(status["downloaded_file_path"]).read_bytes()
    s = attach(api, s, f"security_odex_receipt:{HBL}", slip, "Security deposit slip.pdf")
    with portals.store.read() as data:
        deposit = dict(data["deposits"][0])
    assert (deposit["bl_no"], deposit["utr"], deposit["refund_to"]) == (HBL, "DEMOUTR0001DEP", "Third Party")
    assert deposit["noc_file"] and deposit["receipt_file"]

    # The DO folder takes the portals' documents like the real ones.
    s = attach(api, s, f"hbl:{HBL}", make_pdf(f"HOUSE BILL MSEC-260803047 -251 B/L {MBL}"))
    s = build(api, engine, s)
    assert s["status"] == "folder_ready", [t["note"] for t in s["tasks"]]

    # Ops approve the DO request: the workflow raises it on the local ODeX.
    assert api.post(f"/api/shipments/{s['id']}/tasks/odex_do_request/complete", json={"by": "Ravi"}).status_code == 202
    engine.run()
    s = api.get(f"/api/shipments/{s['id']}").json()
    assert s["status"] == "do_requested", task(s, "odex_do_request")["note"]
    with portals.store.read() as data:
        request = dict(data["do_requests"][0])
    assert (request["bl_no"], request["destuff_type"], request["seaway_bl"]) == (HBL, "Factory", "Yes")
    assert [(d["doc_type"], d["name"]) for d in request["documents"]] == [("All document in a single pdf", f"ALL {HBL[-4:]}.pdf")]
    log = [e["detail"] for e in api.get(f"/api/shipments/{s['id']}/events").json()]
    assert f"DO request raised on ODeX for HBL {HBL}, reference {request['ref']}." in log

    # CMA CGM issues the delivery order; the status check downloads the eDO.
    answer(portals, "do_requests")
    edo = run(api, s, DO_REQUEST_STATUS)[0]
    assert edo["do_request_status"] == "DO Issued"
    text = PdfReader(io.BytesIO(Path(edo["downloaded_file_path"]).read_bytes())).pages[0].extract_text()
    assert HBL in text and "DELIVERY ORDER" in text

    # Every run is in the job's run log: eight steps, the invoice and the deposit checked twice.
    runs = api.get(f"/api/shipments/{s['id']}/runs").json()
    assert len(runs) == 10 and {r["status"] for r in runs} == {"completed"}


def test_the_rtgs_pages_own_checks_stop_a_bad_payment(do_app, portals):
    # The page checks the form in the browser, as the real one does: nothing is
    # sent, and the run fails with the page's own message.
    api, _ = do_app
    fixed = {"do_mailbox": "do-desk@example.com", "payer_account_no": "1234567"}  # one digit short
    assert api.patch("/api/profile", json={"values": fixed}).status_code == 200
    paid = dict(
        do_invoice_no="INV-DEMO-0001", do_invoice_amount="54720.68", payment_utr="DEMOUTR0001INV",
        deposit_utr="DEMOUTR0001DEP", payment_date=(date.today() - timedelta(days=1)).isoformat(),
    )
    s = api.patch(f"/api/shipments/{new_shipment(api)['id']}/values", json={"values": paid}).json()
    with pytest.raises(inngest.NonRetriableError, match="The RTGS page didn't take the payment: Please enter valid Bank A/C Number"):
        run(api, s, RTGS_SHIPMENT)
    with portals.store.read() as data:
        assert data["rtgs_requests"] == []


def tasks_of(api: TestClient, shipment: dict) -> dict[str, dict]:
    return {t["key"]: t for t in api.get(f"/api/shipments/{shipment['id']}").json()["tasks"]}


def approve(api: TestClient, shipment: dict, task_key: str) -> None:
    response = api.post(f"/api/shipments/{shipment['id']}/tasks/{task_key}/complete", json={"by": "Ravi"})
    assert response.status_code == 202, response.text


def mailed(portals: SimpleNamespace, engine: Engine) -> None:
    """CMA CGM's new mail reaches the app's webhook (local_portals/mail.py); the app reads it."""
    assert mail.deliver_pending(portals.store) > 0
    engine.run()


def test_the_process_runs_from_the_mails(process_app, portals):
    # Protects: each portal step starts from its mail or an ops approval, and the job moves through every stage. Fails if a mail, an event or a task stops the chain.
    api, engine = process_app
    fixed = {"do_mailbox": "do-desk@example.com", "payer_account_no": "123456789012", "finance_email": "finance@example.com"}
    assert api.patch("/api/profile", json={"values": fixed}).status_code == 200
    s = new_shipment(api)

    # The MBL is in: the agent requests the DO invoice on ODeX by itself.
    s = attach(api, s, "mbl", make_pdf(f"MASTER BILL {MBL}"))
    engine.run()
    assert tasks_of(api, s)["odex_invoice_request"]["status"] == "completed"

    # The liner's mail says the invoice is out: the agent collects it, and the app mails Finance.
    answer(portals, "invoice_requests")
    mailed(portals, engine)
    with portals.store.read() as data:
        invoice = dict(data["invoice_requests"][0])
    values = api.get(f"/api/shipments/{s['id']}").json()["values"]
    assert (values["do_invoice_no"], float(values["do_invoice_amount"])) == (invoice["invoice_no"], invoice["amount"])
    assert tasks_of(api, s)["finance_mail"]["status"] == "completed"
    [payment_mail] = mail.finance_mails(portals.store)
    assert payment_mail["to"] == "finance@example.com" and invoice["invoice_no"] in payment_mail["subject"]

    # Finance pays and replies with the UTRs. Both confirmations on the RTGS page wait for ops.
    mail.pay(portals.store, payment_mail["id"])
    engine.run()
    values = api.get(f"/api/shipments/{s['id']}").json()["values"]
    assert values["payment_utr"] and values["deposit_utr"] and values["payment_date"] == date.today().isoformat()
    waiting = tasks_of(api, s)
    assert waiting["cma_rtgs_payment"]["status"] == waiting["cma_rtgs_deposit"]["status"] == "awaiting_approval"
    assert waiting["odex_deposit"]["status"] == "pending"  # CMA CGM's security receipt isn't in yet
    approve(api, s, "cma_rtgs_payment")
    approve(api, s, "cma_rtgs_deposit")
    engine.run()
    assert api.get(f"/api/shipments/{s['id']}").json()["status"] == "invoice_paid"

    # CMA CGM's receipts come by mail into the folder; then the deposit waits for ops.
    answer(portals, "rtgs_requests")
    mailed(portals, engine)
    assert tasks_of(api, s)["odex_deposit"]["status"] == "awaiting_approval"
    approve(api, s, "odex_deposit")
    engine.run()
    with portals.store.read() as data:
        deposit = dict(data["deposits"][0])
    assert (deposit["bl_no"], deposit["utr"]) == (HBL, values["deposit_utr"])

    # CMA CGM approves it; ODeX's mail brings the slip for the house bill.
    answer(portals, "deposits")
    mailed(portals, engine)
    s = api.get(f"/api/shipments/{s['id']}").json()
    assert s["status"] == "deposit_approved"

    # The folder and the DO request, as before; then the eDO by mail.
    s = attach(api, s, f"hbl:{HBL}", make_pdf(f"HOUSE BILL MSEC-260803047 -251 B/L {MBL}"))
    s = build(api, engine, s)
    assert s["status"] == "folder_ready", [t["note"] for t in s["tasks"]]
    approve(api, s, "odex_do_request")
    engine.run()
    answer(portals, "do_requests")
    mailed(portals, engine)
    s = api.get(f"/api/shipments/{s['id']}").json()
    assert s["status"] == "edo_received" and s["edo_received_on"] == date.today().isoformat()
    assert {t["key"]: t["status"] for t in s["tasks"]} == {
        key: "completed" for key in (
            "odex_invoice_request", "odex_invoice_collect", "finance_mail", "cma_rtgs_payment", "cma_rtgs_deposit",
            "odex_deposit", "folder_review", "odex_do_request", "odex_edo",
        )
    }
    # Seven runs of the agent, one per portal step: the invoice twice, two payments, the deposit,
    # the DO request and the eDO.
    runs = api.get(f"/api/shipments/{s['id']}/runs").json()
    assert len(runs) == 7 and {r["status"] for r in runs} == {"completed"}
