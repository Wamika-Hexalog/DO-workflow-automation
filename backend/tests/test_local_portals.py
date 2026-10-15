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
from PIL import Image
from pypdf import PdfReader

from conftest import HBL, MBL, make_pdf
from local_portals import cma
from local_portals.main import create_app
from local_portals.odex_screens import OTHER_SESSION
from local_portals.settings import ACCOUNT_NAME

INVOICES = "/odex/epayment/liner-requests"
BL_STATUS = "/odex/epayment/bl-status"
DEPOSITS = "/odex/counter-process/deposit-refund"
DO_REQUESTS = "/odex/epayment/do-request"
RTGS = "/cma-rtgs/index.php/Finance/RTGS/NewRequest/"

# Request New Invoice, as the team's ODeX guide fills it (step 5).
INVOICE_FORM = dict(
    liner_name="CMA CGM AGENCIES INDIA PRIVATE LIMITED", bl_no=MBL, bl_location="NCR", bl_type="Import",
    delivery_type="Factory Destuff", job_no="HXL/IMP/26-27/0431", attachment_type_1="Seaway BL",
    attachment_remarks_1="PLEASE RELEASE TAX INVOICE",
)
# Rows of the RTGS page's invoice grid, as it posts them: Security Deposit,
# Invoice Number, BL Number, Amount, Remarks.
SHIPMENT_ROW = [False, "INV-DEMO-0431", MBL, 54720.68, None]
DEPOSIT_ROW = [True, None, MBL, 40000, None]
SPARE_ROW = [False, None, None, None, None]
# The security deposit, as the team's third guide fills it ("Security deposit number 1").
DEPOSIT_FORM = dict(
    line="CMA CGM AGENCIES INDIA PRIVATE LIMITED", bl_no=HBL, mbl_no=MBL, mode_of_payment="Non-ODeX-RTGS",
    refund_to="ThirdParty", refund_to_name="JAY SHRI RAMDARBAR LOGISTICS PRIVATE LIMITED",
    attachment_name="NOC (on the letter head of MBL consignee)", bank="ICICI BANK", amount="40000.00",
    bank_payment_reference="DEMOUTR0431DEP", payment_date="01-10-2026", cma_ireceipt_no="RCPT-DEMO-00002",
    beneficiary_name="JAY SHRI RAMDARBAR LOGISTICS PRIVATE LIMITED", refund_bank_name="DEMO BANK",
    branch_name="DEMO BRANCH", ac_type="Current", ac_no="000000000001", ifsc_code="DEMO0000001",
)
# The DO request, as the team's second guide fills it ("DO final steps corrected").
DO_REQUEST_FORM = dict(
    bl_no=HBL, liner_issuer="CMA CGM AGENCIES INDIA PRIVATE LIMITED", location="NCR", cargo_type="General",
    destuff_type="Factory", seaway_bl="Yes", payment_not_via_odex="1",
    remarks="Forwarder Letter attached to authorise CHA", hss_customer_name="HEXALOG GLOBAL SUPPLY CHAINS PVT LTD",
    factory_address="PLOT NOS. 92-93, AIHP MILLENIUM UDYOG VIHAR, PHASE-4", factory_pin="122015",
    doc_name_1="All document in a single pdf",
)


def log_in(client: TestClient, user: str = "hexalog-demo", password: str = "local-demo-only"):
    """Signs in on the local ODeX in its two steps, as the real one has them: the User ID
    and password (the captcha is the page's own check), then the company. A login
    signed in elsewhere is asked whether to close that session, and says OK."""
    page = client.post("/odex/login", data={"loginId": user, "password": password, "next": "/odex/home"})
    if 'id="confirmSignIn"' not in page.text:
        return page  # turned down: the login page, saying why
    company = {"compName": ACCOUNT_NAME, "next": "/odex/home"}
    page = client.post("/odex/login/company", data=company)
    if "data-other-session" in page.text:
        page = client.post("/odex/login/company", data={**company, "closeOthers": "1"})
    return page


@pytest.fixture(autouse=True)
def demo_login(monkeypatch):
    """The local ODeX takes its published demo login here, and has no real invoices,
    whatever backend/.env says."""
    monkeypatch.delenv("LOCAL_ODEX_USERNAME", raising=False)
    monkeypatch.delenv("LOCAL_ODEX_PASSWORD", raising=False)
    monkeypatch.delenv("LOCAL_ODEX_INVOICES_DIR", raising=False)


@pytest.fixture()
def web(tmp_path):
    with TestClient(create_app(tmp_path / "portals", autoplay=False, screens_dir=tmp_path / "no-screens")) as client:
        log_in(client)
        yield client


def ask_invoice(web: TestClient, **fields: str):
    """Submits Request New Invoice with the MBL attached as the Seaway BL."""
    attachment = {"attachment_file_1": pdf(f"MBL-{MBL}.pdf", f"MASTER BILL {MBL}")}
    return web.post(f"{INVOICES}/new", data={**INVOICE_FORM, **fields}, files=attachment)


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


def test_odex_signs_in_with_the_password_then_the_company(tmp_path):
    with TestClient(create_app(tmp_path / "portals", autoplay=False, screens_dir=tmp_path / "no-screens")) as web:
        response = web.get("/odex/home", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/odex/login?next=/odex/home"

        # The real login page's parts, under its ids.
        page = web.get("/odex/login").text
        for real_id in ("loginId", "password", "valicode", "toggle", "valiIpt", "signIn", "reset", "invDivMsg"):
            assert f'id="{real_id}"' in page, real_id

        assert "Invalid User ID or Password" in log_in(web, password="guess").text
        assert "Please Enter User ID" in log_in(web, user="").text
        assert "Please Enter Password" in log_in(web, password="").text

        # The password gets the company step, and no further.
        step = web.post("/odex/login", data={"loginId": "hexalog-demo", "password": "local-demo-only", "next": "/odex/home"})
        assert "Welcome" in step.text and 'id="compName"' in step.text and ACCOUNT_NAME in step.text
        assert web.get("/odex/home", follow_redirects=False).status_code == 303
        assert "Please select Company" in web.post("/odex/login/company", data={"compName": "", "next": "/odex/home"}).text
        signed_in = web.post("/odex/login/company", data={"compName": ACCOUNT_NAME, "next": "/odex/home"})
        assert 'id="epayment-location"' in signed_in.text  # the services page

        # One session per login: a second browser is asked before the first is closed.
        with TestClient(web.app) as other:
            other.post("/odex/login", data={"loginId": "hexalog-demo", "password": "local-demo-only", "next": "/odex/home"})
            asked = other.post("/odex/login/company", data={"compName": ACCOUNT_NAME, "next": "/odex/home"})
            assert unescape(re.search(r'data-other-session="([^"]+)"', asked.text)[1]) == OTHER_SESSION
            assert web.get("/odex/home", follow_redirects=False).status_code == 200  # signed in, until the OK
            assert 'id="epayment-location"' in log_in(other).text
        assert web.get("/odex/home", follow_redirects=False).status_code == 303

        # Reset drops a sign-in halfway through.
        web.post("/odex/login", data={"loginId": "hexalog-demo", "password": "local-demo-only", "next": "/odex/home"})
        assert 'id="loginId"' in web.get("/odex/login/reset").text
        assert web.get("/odex/login/company", follow_redirects=False).status_code == 303

        # Only ODeX's own pages are offered after signing in.
        away = web.post(
            "/odex/login", data={"loginId": "hexalog-demo", "password": "local-demo-only", "next": "//elsewhere"},
            follow_redirects=False,
        )
        assert away.headers["location"] == "/odex/login/company?next=/odex/home"


def test_a_demo_shipment_gets_its_real_invoice_from_the_local_odex(web, tmp_path, monkeypatch):
    # Protects: the local ODeX releases the liner's real invoice for its B/L. Fails if the demo invoice goes out instead, or the number, total or date is misread.
    real = tmp_path / "real-invoices"
    real.mkdir()
    (real / "UPC00000001.pdf").write_bytes(make_pdf(
        f"Date: 01-OCT-2026 INIUPC00000001 ORIGINAL* IMPORT INVOICE Bill of Lading: {MBL} "
        "INRTotal Amount: 54,720.68 Carrier No. INCMA0000001"
    ))
    monkeypatch.setenv("LOCAL_ODEX_INVOICES_DIR", str(real))
    ask_invoice(web)
    cma.verify_invoice(web.app.state.store, items(web, "invoice_requests")[0]["id"])
    released = items(web, "invoice_requests")[0]
    assert (released["invoice_no"], released["amount"], released["invoice_date"]) == ("INIUPC00000001", 54720.68, "2026-10-01")
    assert web.app.state.store.file(released["invoice_file"])[1].read_bytes() == (real / "UPC00000001.pdf").read_bytes()


def test_the_odex_guide_from_invoice_request_to_invoice(web):
    # Guide, steps 1-4: the services, ePayment for the location, Requests → Liner Requests.
    services = web.get("/odex/home").text
    assert 'id="epayment-location"' in services and 'id="epayment-go"' in services
    dashboard = web.get("/odex/epayment?location=NCR")
    assert 'id="menu-liner-requests"' in dashboard.text and dashboard.cookies.get("odex_location") == "NCR"
    assert "No invoice requests yet." in web.get(INVOICES).text

    # Step 5: Request New Invoice checks what it's given, then takes it.
    turned_down = outcome(web.post(f"{INVOICES}/new", data={**INVOICE_FORM, "bl_no": "", "delivery_type": ""}))
    assert turned_down["status"] == "rejected"
    assert "Enter the BL no." in turned_down["message"] and "Select the type of delivery." in turned_down["message"]
    assert "Attach the BL attachment." in turned_down["message"]
    listed = ask_invoice(web)
    accepted = outcome(listed)  # a notice on the list, which now shows the request
    assert accepted["status"] == "accepted" and accepted["reference"].startswith("BL")
    assert f">{MBL}<" in listed.text and ">Pending<" in listed.text
    assert "is already there" in outcome(ask_invoice(web))["message"]

    # Steps 6-8: EDO DETAILS finds the request; its invoice isn't out yet.
    status = web.get(f"{BL_STATUS}?bl_no={MBL.lower()}").text
    assert 'id="bl-request"' in status and 'id="bl-invoice"' not in status
    assert "No request for this BL yet." in web.get(f"{BL_STATUS}?bl_no=GGZ0000000").text

    # The liner releases the invoice on ODeX: nothing is emailed (guide, step 10).
    request = items(web, "invoice_requests")[0]
    said = cma.verify_invoice(web.app.state.store, request["id"])
    assert "Rs. 54,720.68" in said  # one container, as on the sample DO receipt
    released = items(web, "invoice_requests")[0]
    assert released["status"] == "Released"
    # ODeX's notice says so, as the real one reads.
    notice = items(web, "outbox")[0]
    assert notice["subject"] == f"ODeX: Invoices Received for {MBL}" and notice["codes"] == []
    assert f"Invoices released for {MBL}," in notice["body"] and released["invoice_no"] in notice["body"]
    with pytest.raises(cma.NotWaiting):
        cma.verify_invoice(web.app.state.store, request["id"])

    # Steps 8-10: the request's page has the invoice, and its PDF downloads.
    status = web.get(f"{BL_STATUS}?bl_no={MBL}").text
    assert 'id="bl-invoice"' in status
    view = web.get(re.search(r'id="bl-request" href="([^"]+)"', status)[1]).text
    assert re.search(r'id="invoice-no"[^>]*>INV-DEMO-\d+<', view)
    assert re.search(r'id="invoice-amount"[^>]*>54720.68<', view) and re.search(r'id="invoice-status"[^>]*>OPEN<', view)
    invoice = web.get(re.search(r'id="invoice-attachment"[^>]*href="([^"]+)"', view)[1])
    assert invoice.headers["content-disposition"].startswith("attachment")
    assert MBL in text_of(invoice.content) and "54,720.68" in text_of(invoice.content)


def test_the_odex_pages_show_the_guides_screenshots_once_set_up(tmp_path):
    screens = tmp_path / "screens"
    screens.mkdir()
    Image.new("RGB", (1430, 683), "white").save(screens / "service.png")
    with TestClient(create_app(tmp_path / "portals", autoplay=False, screens_dir=screens)) as web:
        log_in(web)
        assert 'src="/odex/screens/service.png"' in web.get("/odex/home").text
        assert web.get("/odex/screens/service.png").headers["content-type"] == "image/png"
        # Without its screenshot a page shows a stand-in; its live parts still work.
        dashboard = web.get("/odex/epayment").text
        assert "isn't set up" in dashboard and 'id="edo-search"' in dashboard
        assert web.get("/odex/screens/dashboard.png").status_code == 404


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


def test_the_deposit_follows_the_teams_third_guide(web):
    # Protects: the deposit pages the deposit SOP drives, and what ODeX keeps of a request. Fails if a step's
    # element, the NOC check, the BL search or the request's View changes.
    # Step 1: Counter Process for the place of delivery, then Security Deposit → Security Deposit & Refund.
    services = web.get("/odex/home").text
    assert 'id="counter-location"' in services and 'id="counter-go"' in services
    counter = web.get("/odex/counter-process?location=NCR").text
    assert 'id="menu-security-deposit"' in counter and f'id="menu-deposit-refund" href="{DEPOSITS}"' in counter
    # Step 2: Search Deposit/Refund Request.
    listing = web.get(DEPOSITS).text
    for element in ("search-bl", "search-status", "search-go", "view", "new-deposit"):
        assert f'id="{element}"' in listing, element
    # Step 3: ODeX fills in the BL's details from the IGM. The local one knows only its office's.
    details = web.get(f"{DEPOSITS}/bl-details?bl_no={HBL}&mbl_no={MBL}").json()
    assert (details["known"], details["fpod"], details["office_location"]) == (True, "INTKD6-TUGHLAKABAD", "NCR")
    form = web.get(f"{DEPOSITS}/new").text
    for field in ("line", "bl-no", "mbl-no", "mode-of-payment", "refund-to", "refund-to-name", "refund-to-pan",
                  "attachment-name", "attachment-file", "bank", "amount", "bank-payment-reference", "payment-date",
                  "cma-ireceipt-no", "beneficiary-name", "refund-bank-name", "branch-name", "ac-type", "ac-no",
                  "ifsc-code", "submit-deposit"):
        assert f'id="{field}"' in form, field

    # What the page checks: a refund to a third party needs the NOC, and the payment can't be dated ahead.
    no_noc = outcome(web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM))
    assert no_noc["status"] == "rejected" and "Attach the NOC." in no_noc["message"]
    ahead = outcome(web.post(f"{DEPOSITS}/new", data={**DEPOSIT_FORM, "payment_date": "01-10-2999"}, files={"attachment_file": pdf()}))
    assert "Enter the Payment date as dd-mm-yyyy, not in the future." in ahead["message"]
    assert items(web, "deposits") == []

    # Step 4: SUBMIT. The Request No. shows on the request, found by its BL No. and opened with View.
    accepted = outcome(web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"attachment_file": pdf("NOC.pdf")}))
    assert accepted["status"] == "accepted" and accepted["reference"].startswith("SD")
    found = web.get(DEPOSITS, params={"bl_no": HBL}).text
    assert f'data-bl="{HBL}" data-ref="{accepted["reference"]}" data-status="Deposit Requested"' in found
    deposit = items(web, "deposits")[0]
    view = web.get(f"{DEPOSITS}/{deposit['id']}").text
    assert re.search(r'id="request-no"[^>]*>([^<]*)<', view)[1] == accepted["reference"]
    assert re.search(r'id="request-status"[^>]*>([^<]*)<', view)[1] == "Deposit Requested"
    assert (deposit["utr"], deposit["payment_date"], deposit["ireceipt_no"]) == ("DEMOUTR0431DEP", "2026-10-01", "RCPT-DEMO-00002")
    assert deposit["refund_pan"] and deposit["refund_account"]["ifsc"] == "DEMO0000001"
    assert [(a["doc_type"], a["name"]) for a in deposit["attachments"]] == [("NOC (on the letter head of MBL consignee)", "NOC.pdf")]
    again = outcome(web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"attachment_file": pdf()}))
    assert again["status"] == "rejected" and f"already there: {deposit['ref']}" in again["message"]

    # CMA CGM approves it: the list then offers its slip.
    assert 'class="download-slip"' not in web.get(DEPOSITS).text
    cma.approve_deposit(web.app.state.store, deposit["id"])
    page = web.get(DEPOSITS).text
    assert f'data-bl="{HBL}" data-ref="{deposit["ref"]}" data-status="Deposit Approved"' in page
    link = re.search(r'class="download-slip" href="([^"]+)"', page)[1]
    slip = web.get(link)
    # An attachment, so a browser saves it rather than opening its PDF viewer.
    assert slip.headers["content-disposition"].startswith("attachment")
    assert f"B/L No.: {HBL}" in text_of(slip.content)


def test_a_turned_down_deposit_can_be_raised_again(web):
    web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"attachment_file": pdf()})
    deposit = items(web, "deposits")[0]
    response = web.post(f"/control/reject/deposits/{deposit['id']}", data={"reason": "Receipt unreadable"})
    assert "Receipt unreadable" in unescape(response.text)
    assert items(web, "deposits")[0]["status"] == "Rejected"
    retry = outcome(web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"attachment_file": pdf()}))
    assert retry["status"] == "accepted"


def test_the_do_request_follows_the_teams_second_guide(web):
    # Step 2: Documents → DO Request, on ePayment's main page.
    assert f'id="menu-do-request" href="{DO_REQUESTS}"' in web.get("/odex/epayment?location=NCR").text
    # Step 3: the list opens behind an advertisement, which closes.
    listing = web.get(DO_REQUESTS).text
    assert 'id="advertisement"' in listing and 'id="ad-close"' in listing and 'id="new-do-request"' in listing
    # Step 4: ODeX fills in the Liner/FF-Issuer from the BL No.
    assert web.get(f"{DO_REQUESTS}/bl-liner?bl_no={HBL}").json() == {"liner": "CMA CGM AGENCIES INDIA PRIVATE LIMITED"}
    form = web.get(f"{DO_REQUESTS}/new").text
    for field in ("bl-no", "liner-issuer", "location", "cargo-type", "destuff-type", "seaway-bl", "payment-not-via-odex",
                  "runner-boy", "mobile-no", "remarks", "hss-customer-name", "factory-address", "factory-pin", "add-row", "save"):
        assert f'id="{field}"' in form, field

    # What the page checks before saving.
    folder = pdf(f"ALL {HBL[-4:]}.pdf", f"DO FOLDER {MBL} {HBL}")
    no_rows = {k: v for k, v in DO_REQUEST_FORM.items() if k != "doc_name_1"}
    assert "Add a row and attach the documents" in outcome(web.post(f"{DO_REQUESTS}/new", data=no_rows))["message"]
    unpaid = {**DO_REQUEST_FORM, "payment_not_via_odex": None}
    said = outcome(web.post(f"{DO_REQUESTS}/new", data={k: v for k, v in unpaid.items() if v}, files={"doc_file_1": folder}))
    assert "tick Payment not via ODeX" in said["message"]
    no_liner = {**DO_REQUEST_FORM, "liner_issuer": ""}
    assert "Liner/FF-Issuer is empty" in outcome(web.post(f"{DO_REQUESTS}/new", data=no_liner, files={"doc_file_1": folder}))["message"]
    unnamed = {**DO_REQUEST_FORM, "doc_name_1": ""}
    assert "Document Name in row 1" in outcome(web.post(f"{DO_REQUESTS}/new", data=unnamed, files={"doc_file_1": folder}))["message"]

    # Step 5: SAVE, with the folder as "All document in a single pdf".
    saved = web.post(f"{DO_REQUESTS}/new", data=DO_REQUEST_FORM, files={"doc_file_1": folder})
    accepted = outcome(saved)
    assert accepted["status"] == "accepted" and f"ALL {HBL[-4:]}.pdf" in accepted["message"]
    assert 'id="advertisement"' not in saved.text  # the list after SAVE shows no advertisement
    request = items(web, "do_requests")[0]
    assert (request["bl_no"], request["destuff_type"], request["factory_pin"]) == (HBL, "Factory", "122015")
    assert request["documents"][0]["doc_type"] == "All document in a single pdf"
    twice = outcome(web.post(f"{DO_REQUESTS}/new", data=DO_REQUEST_FORM, files={"doc_file_1": folder}))
    assert twice["status"] == "rejected" and accepted["reference"] in twice["message"]

    # CMA CGM issues the delivery order; the list offers the eDO on the B/L's row.
    cma.issue_edo(web.app.state.store, request["id"])
    found = web.get(f"{DO_REQUESTS}?bl_no={HBL}").text
    assert f'data-search="{HBL}"' in found and f'data-bl="{HBL}"' in found and 'data-status="DO Issued"' in found
    link = re.search(r'class="download-edo" href="([^"]+)"', found)[1]
    edo = text_of(web.get(link).content)
    assert "DELIVERY ORDER" in edo and HBL in edo


def test_autoplay_answers_each_request_once_it_has_waited(web):
    web.post("/control/autoplay", data={"on": "1", "delay_seconds": "5"})
    ask_invoice(web)
    web.post(f"{DO_REQUESTS}/new", data=DO_REQUEST_FORM, files={"doc_file_1": pdf()})
    player = web.app.state.autoplay
    assert player.tick(datetime.now()) == []
    done = player.tick(datetime.now() + timedelta(seconds=6))
    assert len(done) == 2
    assert items(web, "invoice_requests")[0]["status"] == "Released"
    assert items(web, "do_requests")[0]["status"] == "DO Issued"

    web.post("/control/autoplay", data={"on": "0", "delay_seconds": "5"})
    web.post(f"{DEPOSITS}/new", data=DEPOSIT_FORM, files={"receiptFile": pdf(), "nocFile": pdf()})
    assert player.tick(datetime.now() + timedelta(minutes=5)) == []


def test_reset_empties_the_portals(web):
    ask_invoice(web)
    cma.verify_invoice(web.app.state.store, items(web, "invoice_requests")[0]["id"])
    web.post("/control/reset")
    assert items(web, "invoice_requests") == [] and items(web, "outbox") == []
    assert list(web.app.state.store.files_dir.iterdir()) == []


def test_every_page_opens_with_no_demo_banner(web):
    pages = [
        "/", "/control", "/odex/login", "/odex/home", "/odex/epayment?location=NCR", INVOICES, f"{INVOICES}/new",
        f"{BL_STATUS}?bl_no={MBL}", DEPOSITS, f"{DEPOSITS}/new", DO_REQUESTS, f"{DO_REQUESTS}/new", RTGS,
    ]
    for path in pages:
        response = web.get(path)
        assert response.status_code == 200, path
        # The pages look like the real ones: no "Local demo" line across the top.
        assert "Local demo" not in response.text and "(local demo)" not in response.text, path
