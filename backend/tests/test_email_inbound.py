"""Graph webhook and the email-inbound workflow, with Graph itself faked.

msal fetches the Graph token over `requests`, so the HTTP boundary can't be swapped
cheaply. The tests replace make_outlook, the one place the app builds the connector.
"""
import base64
from pathlib import Path
from typing import Any

import inngest
import pytest
from fastapi.testclient import TestClient

from app import containers
from app.main import create_app
from app.workflows import email as email_workflow
from app.workflows.events import EventNames
from tests.conftest import HBL, MBL, Engine, RecordingInngest, make_pdf, new_shipment, settings_for, slot


class FakeOutlook:
    def __init__(self, messages: dict[str, dict[str, Any]], files: dict[str, list[tuple[str, str]]]) -> None:
        self.messages, self.files = messages, files

    async def get_message_detail(self, message_id: str) -> dict[str, Any]:
        return self.messages[message_id]

    async def download_attachments(self, message_id: str, storage_path: Path) -> list[dict[str, Any]]:
        out = []
        for name, text in self.files.get(message_id, [("sheet.pdf", f"MASTER BILL {MBL} sheet 1")]):
            path = Path(storage_path) / name
            path.write_bytes(text.encode() if name.endswith(".rtf") else make_pdf(text))
            out.append({"name": name, "path": str(path)})
        return out


def mail(body: str, subject: str = "re: DO") -> dict[str, Any]:
    return {
        "id": "m1",
        "subject": subject,
        "sender": {"emailAddress": {"address": "kishan@hexalog.in"}},
        "body": {"content": body},
        "hasAttachments": True,
    }


ATTACHMENTS: dict[str, list[tuple[str, str]]] = {}


@pytest.fixture()
def inbox(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    messages: dict[str, dict[str, Any]] = {}
    ATTACHMENTS.clear()
    monkeypatch.setattr(email_workflow, "make_outlook", lambda settings, message_id: FakeOutlook(messages, ATTACHMENTS))
    return messages


def receive(client: RecordingInngest, message_id: str = "m1") -> None:
    client.sent.append(inngest.Event(name=EventNames.EMAIL_INBOUND, data={"message_id": message_id}))


def test_a_utr_mail_is_recorded_on_the_job_and_becomes_a_utr_event(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict
) -> None:
    shipment = new_shipment(api)
    inbox["m1"] = mail(f"Paid.\nDOHACK:utr:{MBL}:SBIN526012345678")
    receive(client)
    engine.run()
    assert EventNames.UTR_RECEIVED in engine.delivered
    history = api.get(f"/api/shipments/{shipment['id']}/events").json()
    assert any("SBIN526012345678" in e["detail"] for e in history)


def test_a_code_in_the_subject_alone_is_enough_when_the_body_is_only_the_forwarded_mail(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict
) -> None:
    shipment = new_shipment(api)
    inbox["m1"] = mail("<p>CMA CGM eBusiness RTGS/NEFT Confirmation</p>", subject=f"FW: DOHACK:utr:{MBL}:100000000002 | RTGS")
    receive(client)
    engine.run()
    assert api.get(f"/api/shipments/{shipment['id']}").json()["values"]["payment_utr"] == "100000000002"


def test_a_document_mail_attaches_its_file_to_the_named_slot(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict
) -> None:
    shipment = new_shipment(api)
    inbox["m1"] = mail(f"DOHACK:document:{MBL}:mbl")
    receive(client)
    engine.run()
    after = api.get(f"/api/shipments/{shipment['id']}").json()
    mbl_slot = next(s for s in after["slots"] if s["id"] == "mbl")
    assert mbl_slot["file"] is not None
    assert EventNames.DOCUMENT_RECEIVED in engine.delivered


def test_one_mail_with_the_mbl_and_the_hbl_files_each_in_its_slot_and_fills_the_numbers(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict
) -> None:
    # Protects: MBL and HBL arrive together; the HBL and container numbers come from the files.
    # Fails if files are not routed by name, or the empty fields are not filled from the text.
    shipment = new_shipment(api, hbl_nos="", containers="")
    inbox["m1"] = mail(f"DOHACK:document:{MBL}")
    ATTACHMENTS["m1"] = [
        ("Master Bill of Lading.pdf", f"MASTER BILL {MBL} container {containers.EXAMPLE}"),
        ("House of Bill of Lading.pdf", f"HOUSE BILL {HBL} B/L {MBL}"),
        ("lunch menu.pdf", "not a shipping document"),
    ]
    receive(client)
    engine.run()
    after = api.get(f"/api/shipments/{shipment['id']}").json()
    assert after["values"]["hbl_nos"] == HBL
    assert containers.EXAMPLE in after["values"]["containers"]
    assert slot(after, "mbl")["file"] is not None
    assert slot(after, f"hbl:{HBL}")["file"] is not None
    assert engine.delivered.count(EventNames.DOCUMENT_RECEIVED) == 2
    assert EventNames.SHIPMENT_UPDATED in engine.delivered


def test_one_forward_with_several_code_lines_fills_the_utr_and_invoice_fields_and_the_receipt_slot(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict
) -> None:
    # Protects: one forwarded RTGS confirmation can carry the UTR, the invoice number and name the
    # slot its receipt goes to. Fails if only the first code line is read, or the fields stay empty.
    mbl = "SHZ1234567"
    shipment = new_shipment(api, mbl_no=mbl, hbl_nos="WOLSZSE12345678A", containers="")
    inbox["m1"] = mail(
        f"DOHACK:utr:{mbl}:100000000001\nDOHACK:invoice:{mbl}:INVDEMO000001\n"
        f"DOHACK:document:{mbl}:do_receipt\nFw: RTGS confirmation"
    )
    ATTACHMENTS["m1"] = [("receipt.pdf", f"RTGS RECEIPT INR 54,720.68 BL {mbl}")]
    receive(client)
    engine.run()
    after = api.get(f"/api/shipments/{shipment['id']}").json()
    assert after["values"]["payment_utr"] == "100000000001"
    assert after["values"]["do_invoice_no"] == "INVDEMO000001"
    assert slot(after, "do_receipt")["file"] is not None


def test_the_draft_mbl_and_two_house_bills_fill_both_hbl_numbers_and_slots(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict
) -> None:
    # Protects: the real draft mail (MBL SHZ1234567, HBLs WOLSZSE12345678A and B). Fails if the
    # HBL number format is not recognised or the second house bill replaces the first.
    mbl = "SHZ1234567"
    shipment = new_shipment(api, mbl_no=mbl, hbl_nos="", containers="")
    inbox["m1"] = mail(f"DOHACK:document:{mbl}")
    ATTACHMENTS["m1"] = [
        ("MBL SHZ1234567 draft.pdf", f"MASTER BILL OF LADING {mbl}"),
        ("HBL A draft.pdf", f"HOUSE BILL WOLSZSE12345678A MBL {mbl}"),
        ("HBL B draft.pdf", f"HOUSE BILL WOLSZSE12345678B MBL {mbl}"),
    ]
    receive(client)
    engine.run()
    after = api.get(f"/api/shipments/{shipment['id']}").json()
    assert after["values"]["hbl_nos"] == "WOLSZSE12345678A\nWOLSZSE12345678B"
    assert slot(after, "hbl:WOLSZSE12345678A")["file"] is not None
    assert slot(after, "hbl:WOLSZSE12345678B")["file"] is not None


def test_mail_without_the_code_starts_nothing(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict
) -> None:
    new_shipment(api)
    inbox["m1"] = mail("Please find the invoice attached.")
    receive(client)
    engine.run()
    assert engine.delivered == [EventNames.EMAIL_INBOUND]


def test_a_code_for_an_unknown_mbl_starts_nothing(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict
) -> None:
    inbox["m1"] = mail("DOHACK:invoice:ZZZ9999999")
    receive(client)
    engine.run()
    assert engine.delivered == [EventNames.EMAIL_INBOUND]


def test_the_graph_handshake_echoes_the_token_as_plain_text(api: TestClient) -> None:
    resp = api.post("/api/webhooks/microsoft-graph?validationToken=abc%20123")
    assert (resp.status_code, resp.text) == (200, "abc 123")
    assert resp.headers["content-type"].startswith("text/plain")


def test_a_notification_becomes_an_inbound_mail_event(
    tmp_path: Path, private_dir: Path, client: RecordingInngest
) -> None:
    app = create_app(settings_for(tmp_path, private_dir, graph_client_state="right"), client)
    notification = {"value": [{"clientState": "right", "resourceData": {"id": "AAMk1"}}]}
    with TestClient(app) as api:
        assert api.post("/api/webhooks/microsoft-graph", json=notification).status_code == 202
    assert [e.name for e in client.sent] == [EventNames.EMAIL_INBOUND]
    assert client.sent[0].data["message_id"] == "AAMk1"


def test_a_notification_with_the_wrong_client_state_is_dropped(
    tmp_path: Path, private_dir: Path, client: RecordingInngest
) -> None:
    app = create_app(settings_for(tmp_path, private_dir, graph_client_state="right"), client)
    notification = {"value": [{"clientState": "wrong", "resourceData": {"id": "AAMk1"}}]}
    with TestClient(app) as api:
        assert api.post("/api/webhooks/microsoft-graph", json=notification).status_code == 202
    assert client.sent == []


def test_graph_is_asked_to_redeliver_when_the_engine_is_down(
    tmp_path: Path, private_dir: Path, client: RecordingInngest
) -> None:
    app = create_app(settings_for(tmp_path, private_dir), client)
    client.down = True
    notification = {"value": [{"resourceData": {"id": "AAMk1"}}]}
    with TestClient(app) as api:
        assert api.post("/api/webhooks/microsoft-graph", json=notification).status_code == 503


def _public_app(tmp_path: Path, private_dir: Path, client: RecordingInngest, **extra: str):  # noqa: ANN202
    settings = settings_for(tmp_path, private_dir, webhook_base_url="https://demo.ngrok-free.app", **extra)
    return create_app(settings, client)


PUBLIC = {"host": "demo.ngrok-free.app"}
LOGIN = {"authorization": "Basic " + base64.b64encode(b"ops:s3cret").decode()}


def test_the_graph_webhook_is_open_on_the_public_host_and_nothing_else_is_without_a_login(
    tmp_path: Path, private_dir: Path, client: RecordingInngest
) -> None:
    with TestClient(_public_app(tmp_path, private_dir, client, basic_auth_users="ops:s3cret")) as api:
        assert api.get("/api/webhooks/microsoft-graph?validationToken=t", headers=PUBLIC).text == "t"
        assert api.get("/api/shipments", headers=PUBLIC).status_code == 401
        assert api.get("/api/webhooks/status", headers=PUBLIC).status_code == 401
        assert api.get("/api/shipments", headers={**PUBLIC, "authorization": "Basic " + base64.b64encode(b"ops:wrong").decode()}).status_code == 401
        assert api.get("/api/shipments", headers={**PUBLIC, **LOGIN}).status_code == 200


def test_the_inngest_endpoint_is_never_served_on_the_public_host_even_with_a_login(
    tmp_path: Path, private_dir: Path, client: RecordingInngest
) -> None:
    with TestClient(_public_app(tmp_path, private_dir, client, basic_auth_users="ops:s3cret")) as api:
        assert api.post("/api/inngest", headers={**PUBLIC, **LOGIN}).status_code == 404


def test_with_no_logins_configured_the_public_host_serves_only_the_graph_webhook(
    tmp_path: Path, private_dir: Path, client: RecordingInngest
) -> None:
    with TestClient(_public_app(tmp_path, private_dir, client)) as api:
        assert api.get("/api/webhooks/microsoft-graph?validationToken=t", headers=PUBLIC).text == "t"
        assert api.get("/api/shipments", headers={**PUBLIC, **LOGIN}).status_code == 404


def test_the_same_api_needs_no_login_when_reached_locally(
    tmp_path: Path, private_dir: Path, client: RecordingInngest
) -> None:
    with TestClient(_public_app(tmp_path, private_dir, client, basic_auth_users="ops:s3cret")) as api:
        assert api.get("/api/shipments").status_code == 200


HBL_PAGE = """B / L No.
{hbl}
Consignee(Complete name and address)
ENVOGUE STYLES GLOBAL LLP
Ocean vessel.
OOCL SINGAPORE
V.0FBI5W1MA
CNTR NO.:
{box}/J2505844/40'HQ/150PACKAGES"""


def test_the_draft_mail_opens_the_job_and_fills_its_fields_from_the_bills_including_an_rtf_mbl(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict
) -> None:
    # Protects: the first mail about a shipment creates the job with the facts printed on the bills.
    # Fails if no job is made for an unknown MBL, the RTF MBL is skipped, or a field stays empty.
    mbl = "SHZ1234567"
    inbox["m1"] = mail(f"DOHACK:document:{mbl}")
    ATTACHMENTS["m1"] = [
        ("12345678A HBL.pdf", HBL_PAGE.format(hbl="WOLSZSE12345678A", box=containers.EXAMPLE)),
        ("12345678B HBL.pdf", HBL_PAGE.format(hbl="WOLSZSE12345678B", box=containers.EXAMPLE)),
        ("MBL DRAFT.rtf", "{\\rtf1\\ansi Shipper X\\par " + mbl + "\\par Consignee(Complete name and address) HEXALOG GLOBAL SUPPLY CHAINS PVT LTD.\\par Ocean vessel. OOCL SINGAPORE V.0FBI5W1MA\\par}"),
    ]
    receive(client)
    engine.run()
    (job,) = [j for j in api.get("/api/shipments").json() if j["values"]["mbl_no"] == mbl]
    assert job["values"]["hbl_nos"] == "WOLSZSE12345678A\nWOLSZSE12345678B"
    assert (job["values"]["vessel"], job["values"]["voyage"]) == ("OOCL SINGAPORE", "0FBI5W1MA")
    assert job["values"]["consignee_mbl"] == "HEXALOG GLOBAL SUPPLY CHAINS PVT LTD"
    assert containers.EXAMPLE in job["values"]["containers"]
    for slot_id in ("mbl", "hbl:WOLSZSE12345678A", "hbl:WOLSZSE12345678B"):
        assert slot(job, slot_id)["file"] is not None
    assert not any(f["kind"] == "fields" for s in job["slots"] for f in s["flags"]), "letters still need fields"


def test_a_second_draft_mail_for_the_same_mbl_does_not_open_a_second_job(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict
) -> None:
    mbl = "SHZ1234567"
    for mid in ("m1", "m2"):
        inbox[mid] = mail(f"DOHACK:document:{mbl}")
        ATTACHMENTS[mid] = [("12345678A HBL.pdf", HBL_PAGE.format(hbl="WOLSZSE12345678A", box=containers.EXAMPLE))]
        receive(client, mid)
        engine.run()
    assert len([j for j in api.get("/api/shipments").json() if j["values"]["mbl_no"] == mbl]) == 1
