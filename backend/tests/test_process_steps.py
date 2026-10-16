"""The Process page's steps (app/process_steps.py): every step of the job in order, where
each one stands, and since when a step has waited."""
import inngest
from fastapi.testclient import TestClient

from app.main import create_app
from app.workflows.events import EventNames
from tests.conftest import MBL, Engine, RecordingInngest, make_pdf, new_shipment, settings_for, sign_in

NUMBERS = ["1", "2", "3", "4", "5", "6", "7a", "7b", "7c", "8", "9", "10", "11", "12"]


def test_every_step_shows_in_order_and_a_wait_on_finance_carries_its_clock(tmp_path, private_dir):
    # Protects: the Process page lists every step whatever the job has reached, says which steps were done before
    # the app ran them, and dates a wait on Finance. Fails if a step is left out or out of order, or a wait has no clock.
    client = RecordingInngest()
    app = create_app(settings_for(tmp_path, private_dir, automate_portals=True), client)
    with TestClient(app) as api:
        sign_in(api)
        engine = Engine(app, client)
        s = new_shipment(api, do_invoice_no="INV-DEMO-00001", do_invoice_amount="54720.68")
        process = api.get(f"/api/shipments/{s['id']}").json()["process"]
        assert [step["number"] for step in process] == NUMBERS
        assert process[0]["state"] == "done" and process[5]["state"] == "not_started"

        # The invoice came in before the app ran its steps; then the app mails Finance and waits for the UTRs.
        assert api.patch("/api/profile", json={"values": {"finance_email": "finance@example.com"}}).status_code == 200
        invoice = app.state.rt.settings.storage_dir / s["id"] / "portal" / f"DO-invoice-{MBL}.pdf"
        invoice.parent.mkdir(parents=True)
        invoice.write_bytes(make_pdf(f"TAX INVOICE INV-DEMO-00001 BL {MBL}"))
        client.sent.append(inngest.Event(name=EventNames.INVOICE_COLLECTED, data={"shipment_id": s["id"]}))
        engine.run()
        steps = {step["number"]: step for step in api.get(f"/api/shipments/{s['id']}").json()["process"]}
    assert (steps["2"]["state"], steps["2"]["note"]) == ("done", "Done outside the app.")
    assert (steps["3"]["state"], steps["3"]["note"]) == ("done", "Invoice INV-DEMO-00001 is released.")
    assert steps["5"]["state"] == "done"
    utrs = steps["6"]
    assert (utrs["state"], utrs["waiting_on"]) == ("waiting", "finance") and utrs["since"] == steps["5"]["at"]
    # A step with its task waiting for data says what it needs; one the job hasn't reached isn't started.
    assert steps["7a"]["state"] == "pending" and "needs UTR for the shipment payment" in steps["7a"]["note"]
    assert steps["11"]["state"] == "not_started"
