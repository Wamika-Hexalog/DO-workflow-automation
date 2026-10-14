"""The local portals: their forms check what they're sent the way a portal would,
CMA CGM's answers (the control panel) move requests on, and every document they
hand out carries the B/L numbers the DO folder's checks look for."""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timedelta
from html import unescape

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from conftest import HBL, MBL, make_pdf
from local_portals import cma
from local_portals.main import create_app

INVOICES = "/odex/request/liner-request"
DEPOSITS = "/odex/counter-process/deposit-refund"
DO_REQUESTS = "/odex/e-payment/do-request"
RTGS = "/cma-rtgs/index.php/Finance/RTGS/NewRequest/"

INVOICE_FORM = dict(
    liner="CMA CGM", invoiceType="Delivery Order", blNo=MBL, containers="TCNU5191327",
    email="do-desk@example.com", remarks="HXL/IMP/26-27/0431",
)
# Rows of the RTGS page's invoice grid, as it posts them: Security Deposit,
# Invoice Number, BL Number, Amount, Remarks.
SHIPMENT_ROW = [False, "INV-DEMO-0431", MBL, 54720.68, None]
DEPOSIT_ROW = [True, None, MBL, 40000, None]
SPARE_ROW = [False, None, None, None, None]
DEPOSIT_FORM = dict(
    liner="CMA CGM", blNo=HBL, amount="40000", paymentMode="RTGS/NEFT", utrNo="DEMOUTR0431DEP",
    paymentDate="2026-10-01", bankName="ICICI BANK", refundTo="Third Party",
    thirdPartyName="JAY SHRI RAM DARBAR LOGISTICS (P) LTD",
)
DO_REQUEST_FORM = dict(
    liner="CMA CGM", blNo=HBL, doType="E-DO", deliveryType="Factory De-stuff", containers="TCNU5191327",
    remarks="HXL/IMP/26-27/0431", docType1="DO folder (combined PDF)",
)


@pytest.fixture()
def web(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCAL_ODEX_USERNAME", raising=False)
    monkeypatch.delenv("LOCAL_ODEX_PASSWORD", raising=False)
    with TestClient(create_app(tmp_path / "portals", autoplay=False)) as client:
        client.post("/odex/login", data={"username": "hexalog-demo", "password": "local-demo-only", "next": "/odex/home"})
        yield client


def rtgs_form(rows: list[list], **fields: str | None) -> dict[str, str]:
    """The RTGS Confirmation form as the real page posts it: the whole grid, spare
    rows included, goes as invoice_data. A field given as None isn't sent, like an
    unticked box."""
    form = {
        "shipcomp_code": "0001", "company_name": "HEXALOG GLOBAL SUPPLY CHAINS PVT LTD",
        "contact_name": "Kishan Rawat", "contact_number": "8802757004",
        "contact_email": "kishan@example.com,customs-ops@example.com", "utr_number": "DEMOUTR0431INV",
        "bank_account_number": "123456789012", "transfer_date": "01-10-2026", "currency": "INR",
        "amount_transfered": "54720.68", "invoice_data": json.dumps({"data": [*rows, SPARE_ROW, SPARE_ROW]}),
        "accept_tc": "on", "submit_rtgs": "Submit", **fields,
    }
    return {key: value for key, value in form.items() if value is not None}


def rtgs_answer(response) -> dict[str, str]:
    """What the RTGS page made of a request, as the SOP reads it."""
    if "Submitted Successfully" in response.text:
        return {"status": "accepted", "reference": re.search(r"Request Reference (\d+)", response.text)[1]}
    found = re.search(r'<p class="alert alert-danger">(.*?)</p>', response.text, re.S)
    assert found, response.text
    return {"status": "rejected", "message": unescape(found[1])}


def outcome(response) -> dict[str, str]:
    """What a form's answer says, as the SOPs read it."""
    found = re.search(r'id="outcome"[^>]*data-status="(\w+)" data-reference="([^"]*)" data-message="([^"]*)"', response.text)
    assert found, response.text
    return {"status": found[1], "reference": found[2], "message": unescape(found[3])}


def items(web: TestClient, kind: str) -> list[dict]:
    with web.app.state.store.read() as data:
        return [dict(item) for item in data[kind]]


def pdf(name: str = "file.pdf", text: str = "A DOCUMENT") -> tuple[str, bytes, str]:
    return name, make_pdf(text), "application/pdf"


def text_of(content: bytes) -> str:
    return " ".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)


def stored_text(web: TestClient, file_id: str) -> str:
    _, path = web.app.state.store.file(file_id)
    return text_of(path.read_bytes())


def test_odex_asks_for_the_login(tmp_path):
    with TestClient(create_app(tmp_path / "portals", autoplay=False)) as web:
        response = web.get("/odex/home", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/odex/login?next=/odex/home"

        wrong = web.post("/odex/login", data={"username": "hexalog-demo", "password": "guess", "next": "/odex/home"})
        assert 'id="login-error"' in wrong.text

        home = web.post("/odex/login", data={"username": "hexalog-demo", "password": "local-demo-only", "next": "/odex/home"})
        assert 'id="menu"' in home.text and "Local demo." in home.text
        # The login page only offers the next page within ODeX.
        away = web.post("/odex/login", data={"username": "hexalog-demo", "password": "local-demo-only", "next": "//elsewhere"}, follow_redirects=False)
        assert away.headers["location"] == "/odex/home"


def test_an_invoice_request_is_checked_then_verified_by_the_liner(web):
    turned_down = outcome(web.post(f"{INVOICES}/new", data={**INVOICE_FORM, "containers": "TCNU51913", "email": "x"}))
    assert turned_down["status"] == "rejected"
    assert "Not a container number: TCNU51913." in turned_down["message"]
    assert "Enter one to three email addresses for the invoice, separated by commas." in turned_down["message"]

    accepted = outcome(web.post(f"{INVOICES}/new", data=INVOICE_FORM))
    assert accepted["status"] == "accepted" and accepted["reference"].startswith("LR")
    twice = outcome(web.post(f"{INVOICES}/new", data=INVOICE_FORM))
    assert twice["status"] == "rejected" and accepted["reference"] in twice["message"]

    request = items(web, "invoice_requests")[0]
    said = cma.verify_invoice(web.app.state.store, request["id"])
    assert "Rs. 54,720.68" in said  # one container, as on the sample DO receipt
    request = items(web, "invoice_requests")[0]
    assert request["status"] == "Invoice Verified"
    mail = items(web, "outbox")[0]
    assert mail["to"] == "do-desk@example.com" and request["invoice_no"] in mail["subject"]
    invoice = stored_text(web, mail["attachments"][0])
    assert MBL in invoice and "54,720.68" in invoice and "LOCAL DEMO" in invoice
    with pytest.raises(cma.NotWaiting):
        cma.verify_invoice(web.app.state.store, request["id"])


def test_each_payment_is_confirmed_on_its_own_with_its_receipt(web):
    page = web.get(RTGS).text
    assert "RTGS Confirmation (CMA CGM) - New Request" in page
    for real_id in ("company_name", "contact_email", "utr_number", "bank_account_number", "transfer_date",
                    "amount_transfered", "invoice_table", "accept_tc", "submit_rtgs"):
        assert f'id="{real_id}"' in page, real_id
    assert "RTGS Confirmation (ANL) - New Request" in web.get(f"{RTGS}?shipcomp_code=0002").text

    response = web.post(RTGS, data=rtgs_form([SHIPMENT_ROW]))
    shipment = rtgs_answer(response)
    assert shipment["status"] == "accepted" and shipment["reference"].isdigit()
    assert "RTGS Confirmation Request (CMA CGM) Submitted Successfully" in response.text
    assert "01-Oct-2026" in response.text and "INR  54720.68" in response.text
    deposit = rtgs_answer(web.post(RTGS, data=rtgs_form([DEPOSIT_ROW], utr_number="DEMOUTR0431DEP", amount_transfered="40000")))
    assert deposit["status"] == "accepted" and deposit["reference"] != shipment["reference"]
    first, second = items(web, "rtgs_requests")
    assert first["invoices"] == [
        {"security_deposit": False, "invoice_no": "INV-DEMO-0431", "bl_no": MBL, "amount": 54720.68, "remarks": ""}
    ]
    assert second["invoices"] == [{"security_deposit": True, "invoice_no": "", "bl_no": MBL, "amount": 40000.0, "remarks": ""}]

    store = web.app.state.store
    for request in (first, second):
        cma.send_receipts(store, request["id"])
    deposit_mail, shipment_mail = items(web, "outbox")  # newest first
    assert shipment_mail["to"] == "kishan@example.com, customs-ops@example.com"
    do_receipt = stored_text(web, shipment_mail["attachments"][0])
    assert "DO RECEIPT" in do_receipt and "INV-DEMO-0431" in do_receipt and MBL in do_receipt
    assert "DEMOUTR0431INV" in do_receipt and "123456789012" in do_receipt
    assert "Fifty-Four Thousand Seven Hundred and Twenty Rupees and Sixty-Eight Paise Only" in do_receipt
    security_receipt = stored_text(web, deposit_mail["attachments"][0])
    assert "SECURITY RECEIPT" in security_receipt and MBL in security_receipt and "40,000.00" in security_receipt


def test_the_rtgs_page_checks_the_payment_and_its_rows(web):
    def problems(rows: list[list] = (SHIPMENT_ROW,), **fields: str) -> str:
        answer = rtgs_answer(web.post(RTGS, data=rtgs_form(list(rows), **fields)))
        assert answer["status"] == "rejected"
        return answer["message"]

    # The real page's own messages.
    assert "Please enter valid Email(s)" in problems(contact_email="a@x.com,b@x.com,c@x.com,d@x.com")
    assert "Please enter Transfer Date" in problems(transfer_date="2026-10-01")
    assert "Please enter Transfer Date" in problems(transfer_date="01-10-2999")
    assert "Please enter valid Bank A/C Number" in problems(bank_account_number="ICICI-1")
    assert "You have to agree to our privacy policy" in problems(accept_tc=None)
    # The local page's own checks of the grid.
    assert "Row 1: a security deposit has no invoice number." in problems([[True, "INV-1", MBL, 40000, None]], amount_transfered="40000")
    assert "Row 1: enter the invoice number, or tick Security Deposit." in problems([[False, None, MBL, 54720.68, None]])
    assert "The rows add up to 54,720.68, not the 50,000.00 transferred." in problems(amount_transfered="50000")
    assert "Enter the invoice details" in problems([])
    assert items(web, "rtgs_requests") == []

    # The same UTR can't be confirmed twice.
    assert rtgs_answer(web.post(RTGS, data=rtgs_form([SHIPMENT_ROW])))["status"] == "accepted"
    assert "UTR Number DEMOUTR0431INV is already submitted" in problems()


def test_a_deposit_needs_its_receipt_and_for_a_third_party_the_noc(web):
    no_noc = outcome(web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"receiptFile": pdf()}))
    assert no_noc["status"] == "rejected" and "Attach the consignee's NOC." in no_noc["message"]
    not_pdf = outcome(web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"receiptFile": ("r.txt", b"hello", "text/plain"), "nocFile": pdf()}))
    assert "The security deposit receipt must be a PDF." in not_pdf["message"]
    # Nothing was kept from the turned-down forms.
    assert items(web, "deposits") == []

    to_consignee = {**DEPOSIT_FORM, "refundTo": "Consignee", "thirdPartyName": ""}
    accepted = outcome(web.post(f"{DEPOSITS}/new", data=to_consignee, files={"receiptFile": pdf()}))
    assert accepted["status"] == "accepted" and accepted["reference"].startswith("SD")
    again = outcome(web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"receiptFile": pdf(), "nocFile": pdf()}))
    assert again["status"] == "rejected" and f"already registered for B/L {HBL}" in again["message"]

    deposit = items(web, "deposits")[0]
    assert 'class="download-slip"' not in web.get(DEPOSITS).text
    cma.approve_deposit(web.app.state.store, deposit["id"])
    page = web.get(DEPOSITS).text
    assert f'data-bl="{HBL}" data-status="Deposit Approved"' in page
    link = re.search(r'class="download-slip" href="([^"]+)"', page)[1]
    slip = web.get(link)
    # An attachment, so a browser saves it rather than opening its PDF viewer.
    assert slip.headers["content-disposition"].startswith("attachment")
    assert f"B/L No.: {HBL}" in text_of(slip.content)


def test_a_turned_down_deposit_can_be_raised_again(web):
    web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"receiptFile": pdf(), "nocFile": pdf()})
    deposit = items(web, "deposits")[0]
    response = web.post(f"/control/reject/deposits/{deposit['id']}", data={"reason": "Receipt unreadable"})
    assert "Receipt unreadable" in unescape(response.text)
    assert items(web, "deposits")[0]["status"] == "Rejected"
    retry = outcome(web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"receiptFile": pdf(), "nocFile": pdf()}))
    assert retry["status"] == "accepted"


def test_a_do_request_takes_the_folder_through_add_row(web):
    no_rows = {k: v for k, v in DO_REQUEST_FORM.items() if k != "docType1"}
    assert "Add a row and attach the DO documents." in outcome(web.post(f"{DO_REQUESTS}/new", data=no_rows))["message"]

    folder = pdf("HXL-IMP-26-27-0431-GGZ3193024-DO-folder.pdf", f"DO FOLDER {MBL}")
    accepted = outcome(web.post(f"{DO_REQUESTS}/new", data=DO_REQUEST_FORM, files={"docFile1": folder}))
    assert accepted["status"] == "accepted"
    assert "HXL-IMP-26-27-0431-GGZ3193024-DO-folder.pdf" in accepted["message"]
    twice = outcome(web.post(f"{DO_REQUESTS}/new", data=DO_REQUEST_FORM, files={"docFile1": folder}))
    assert twice["status"] == "rejected" and accepted["reference"] in twice["message"]

    cma.issue_edo(web.app.state.store, items(web, "do_requests")[0]["id"])
    link = re.search(r'class="download-edo" href="([^"]+)"', web.get(DO_REQUESTS).text)[1]
    edo = text_of(web.get(link).content)
    assert "DELIVERY ORDER" in edo and HBL in edo and "TCNU5191327" in edo


def test_autoplay_answers_each_request_once_it_has_waited(web):
    web.post("/control/autoplay", data={"on": "1", "delay_seconds": "5"})
    web.post(f"{INVOICES}/new", data=INVOICE_FORM)
    web.post(f"{DO_REQUESTS}/new", data=DO_REQUEST_FORM, files={"docFile1": pdf()})
    player = web.app.state.autoplay
    assert player.tick(datetime.now()) == []
    done = player.tick(datetime.now() + timedelta(seconds=6))
    assert len(done) == 2
    assert items(web, "invoice_requests")[0]["status"] == "Invoice Verified"
    assert items(web, "do_requests")[0]["status"] == "DO Issued"

    web.post("/control/autoplay", data={"on": "0", "delay_seconds": "5"})
    web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"receiptFile": pdf(), "nocFile": pdf()})
    assert player.tick(datetime.now() + timedelta(minutes=5)) == []


def test_reset_empties_the_portals(web):
    web.post(f"{INVOICES}/new", data=INVOICE_FORM)
    cma.verify_invoice(web.app.state.store, items(web, "invoice_requests")[0]["id"])
    web.post("/control/reset")
    assert items(web, "invoice_requests") == [] and items(web, "outbox") == []
    assert list(web.app.state.store.files_dir.iterdir()) == []


def test_every_page_says_it_is_a_local_demo(web):
    pages = ["/", "/control", INVOICES, f"{INVOICES}/new", DEPOSITS, f"{DEPOSITS}/new", DO_REQUESTS, f"{DO_REQUESTS}/new", RTGS]
    for path in pages:
        response = web.get(path)
        assert response.status_code == 200, path
        assert 'id="demo-banner"' in response.text, path
