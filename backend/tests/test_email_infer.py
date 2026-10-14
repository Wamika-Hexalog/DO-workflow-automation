"""The three demo mails, as received, read with only the short marker."""
import pytest
from fastapi.testclient import TestClient

from app.email_infer import infer_codes, marker_present
from app.workflows.events import EventNames
from tests.conftest import Engine, RecordingInngest, make_pdf, new_shipment, slot
from tests.test_email_inbound import ATTACHMENTS, inbox, mail, receive  # noqa: F401 - the fixture

HBL_PATTERN = r"(?:WOLSZSE\d{8}[A-Z]|MSEC\d{12})"

RTGS_INVOICE = """CMA CGM eBusiness
RTGS/NEFT Confirmation Request Processed - 10000001
Status:  Request Confirmed
Request Details
Request Id: 10000001
UTR: 100000000001
Date of Transfer: 03-Oct-2026
Amount Transferred: INR 54,720.68
Invoice Details
Invoice Ref. BL Number I/E Amount
INVDEMO000001\tSHZ1234567\tI\t54,720.68
"""
RTGS_DEPOSIT = RTGS_INVOICE.replace("100000000001", "100000000002").replace("INVDEMO000001", "-").replace("54,720.68", "40,000.00")
SLIP = """Please find the attached security slip issued by CMA CGM AGENCIES INDIA PRIVATE LIMITED
BL No. - WOLSZSE12345678A
Container - ECMU1234565
Instrument No. - 100000000001
Status - Deposit Approved
"""
DRAFT = """Please find the attached revised Drafts documents for your review & approval.
MBL and HBL & Invoice Number.
MBL HBL Invoice Number
SHZ1234567 WOLSZSE12345678A 2026/ML/21-08/01
SHZ1234567 WOLSZSE12345678B 2026/ML/21-08/02
"""


def facts(subject: str, body: str) -> list[tuple[str, str, str | None]]:
    return [(c.stage, c.mbl_no, c.arg) for c in infer_codes(subject, body, hbl_pattern=HBL_PATTERN)]


def test_an_rtgs_confirmation_with_an_invoice_gives_the_utr_the_invoice_and_the_do_receipt() -> None:
    assert facts("#DOHACK Fw: RTGS", RTGS_INVOICE) == [
        ("utr", "SHZ1234567", "100000000001"),
        ("invoice", "SHZ1234567", "INVDEMO000001"),
        ("document", "SHZ1234567", "do_receipt"),
    ]


def test_an_rtgs_confirmation_without_an_invoice_is_the_deposit_and_its_security_receipt() -> None:
    assert facts("#DOHACK Fw: RTGS", RTGS_DEPOSIT) == [
        ("utr", "SHZ1234567", "deposit:100000000002"),
        ("document", "SHZ1234567", "security_receipt"),
    ]


def test_the_security_slip_names_the_house_bill_whose_slot_it_fills() -> None:
    assert facts("#DOHACK Fw: ODeX: Security slip - SD2026000000001", SLIP) == [
        ("document", "WOLSZSE12345678A", "security_odex_receipt:WOLSZSE12345678A")
    ]


def test_the_draft_mail_gives_the_mbl_once_with_no_slot() -> None:
    assert facts("Savana draft documents SHZ1234567/WOLSZSE12345678A&B MBL & HBL", DRAFT) == [
        ("document", "SHZ1234567", None)
    ]


def test_ordinary_mail_gives_nothing() -> None:
    assert facts("Lunch?", "Are you free at 1pm to talk about the MBL?") == []


@pytest.mark.parametrize(
    ("marker", "subject", "present"),
    [
        ("#DOHACK", "#DOHACK Fw: x", True),
        ("#DOHACK", "Fw: #dohack x", True),
        ("#DOHACK", "#DOHACKED", False),
        ("#DOHACK", "Fw: x", False),
        ("", "Fw: x", True),
    ],
)
def test_the_marker_is_a_whole_word_and_an_empty_marker_allows_any_mail(marker: str, subject: str, present: bool) -> None:
    assert marker_present(marker, subject, "") is present


def test_a_marked_rtgs_forward_fills_the_fields_and_the_receipt_slot_with_no_code_lines(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict  # noqa: F811
) -> None:
    shipment = new_shipment(api, mbl_no="SHZ1234567", hbl_nos="WOLSZSE12345678A", containers="")
    inbox["m1"] = mail(RTGS_INVOICE, subject="#DOHACK Fw: CMA CGM India - RTGS/NEFT Payment Confirmation - Request Id: 10000001")
    ATTACHMENTS["m1"] = [("Receipt.pdf", "RTGS RECEIPT BL SHZ1234567")]
    receive(client)
    engine.run()
    after = api.get(f"/api/shipments/{shipment['id']}").json()
    assert after["values"]["payment_utr"] == "100000000001"
    assert after["values"]["do_invoice_no"] == "INVDEMO000001"
    assert slot(after, "do_receipt")["file"] is not None


def test_a_marked_security_slip_goes_to_the_job_that_has_that_house_bill(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict  # noqa: F811
) -> None:
    other = new_shipment(api, mbl_no="GGZ3193024", hbl_nos="MSEC260803047251")
    mine = new_shipment(api, mbl_no="SHZ1234567", hbl_nos="WOLSZSE12345678A\nWOLSZSE12345678B")
    inbox["m1"] = mail(SLIP, subject="#DOHACK Fw: ODeX: Security slip - SD2026000000001")
    ATTACHMENTS["m1"] = [("slip.pdf", "ODeX security slip BL No. WOLSZSE12345678A")]
    receive(client)
    engine.run()
    assert slot(api.get(f"/api/shipments/{mine['id']}").json(), "security_odex_receipt:WOLSZSE12345678A")["file"] is not None
    assert all(s["file"] is None for s in api.get(f"/api/shipments/{other['id']}").json()["slots"] if s["doc_key"] == "security_odex_receipt")
    assert EventNames.DOCUMENT_RECEIVED in engine.delivered


def test_unmarked_mail_is_ignored_even_when_it_looks_like_an_rtgs_confirmation(
    api: TestClient, engine: Engine, client: RecordingInngest, inbox: dict  # noqa: F811
) -> None:
    new_shipment(api, mbl_no="SHZ1234567")
    inbox["m1"] = mail(RTGS_INVOICE, subject="Fw: RTGS/NEFT Payment Confirmation")
    receive(client)
    engine.run()
    assert engine.delivered == [EventNames.EMAIL_INBOUND]
