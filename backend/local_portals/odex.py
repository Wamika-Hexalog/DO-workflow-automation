"""The local ODeX: its login, its files, and the plain pages for the steps no
screenshot shows yet (the SOP sheet of DO PROCESS FLOW E2E.xlsx):

    Counter Process → Deposit and Refund → New Deposit
    e-Payment → DO Request → New DO Request

The pages the team's ODeX guide shows (from the services page to the invoice) are
screenshots with live buttons over them, in odex_screens.py.

Requests then wait for CMA CGM, which the control panel plays. Every form answers
with an #outcome element the SOPs read (pages.outcome), and each list marks its
rows with data-bl, data-ref and data-status for the SOPs that check a request and
download its slip or eDO. These pages' real fields aren't known yet: they follow
the SOP sheet and the sample documents.
"""
from __future__ import annotations

import json
import secrets
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from . import forms, pages
from .pages import esc
from .settings import default_login, odex_login
from .store import Store, next_ref, now

router = APIRouter(prefix="/odex")

COOKIE = "odex_session"
LINERS = ("CMA CGM", "COSCO", "Hapag-Lloyd", "Maersk", "MSC", "ONE")
PAYMENT_MODES = ("RTGS/NEFT", "DD/Pay Order", "Cheque")
REFUND_TO = ("Consignee", "Third Party")
DO_TYPES = ("E-DO", "D/O")
DELIVERY_TYPES = ("Factory De-stuff", "CFS De-stuff", "LCL", "DPD")
DOC_TYPES = ("DO folder (combined PDF)", "Bill of lading", "Receipt", "NOC", "Other")
INVOICES = "/odex/epayment/liner-requests"
DEPOSITS = "/odex/counter-process/deposit-refund"
DO_REQUESTS = "/odex/e-payment/do-request"

MENU = pages.menu(
    [
        ("request", "Request", [("liner-request", "Liner Requests", INVOICES)]),
        ("counter-process", "Counter Process", [("deposit-refund", "Deposit and Refund", DEPOSITS)]),
        ("e-payment", "e-Payment", [("do-request", "DO Request", DO_REQUESTS)]),
    ]
)


# Helpers ----------------------------------------------------------------------


def store_of(request: Request) -> Store:
    return request.app.state.store


def current_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with store_of(request).read() as data:
        return data["sessions"].get(token)


def to_login(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/odex/login?next={quote(request.url.path)}", status_code=303)


def _page(title: str, body: str, user: str, *, script: str = "", status_code: int = 200) -> Response:
    signed_in = f'<span class="user">{esc(user)} · <a id="logout" href="/odex/logout">Log out</a></span>'
    return pages.page(title, body, site="odex", menu=MENU, user=signed_in, script=script, status_code=status_code)


def _answer(
    user: str, title: str, path: str, back: tuple[str, str], *, ref: str = "", message: str = "",
    problems: list[str] | None = None,
) -> Response:
    """The page after a form: what ODeX made of it."""
    accepted = problems is None
    said = message if accepted else "The request was not submitted."
    body = (
        f'<h1>{esc(title)}</h1><p class="path">{esc(path)}</p>'
        f"{pages.outcome(accepted, said, reference=ref, problems=problems or [])}"
        f'<p><a class="btn" id="back" href="{esc(back[0])}">{esc(back[1])}</a></p>'
    )
    return _page(title, body, user, status_code=200 if accepted else 400)


def _file_link(file_id: str | None, text: str, css: str) -> str:
    return f'<a class="{css}" href="/odex/files/{esc(file_id)}">{esc(text)}</a>' if file_id else ""


def _row(item: dict[str, Any], cells: list[str]) -> str:
    return (
        f'<tr data-ref="{esc(item["ref"])}" data-bl="{esc(item["bl_no"])}" data-status="{esc(item["status"])}">'
        + "".join(f"<td>{cell}</td>" for cell in cells)
        + "</tr>"
    )


def _when(item: dict[str, Any]) -> str:
    return esc(item["created_at"].replace("T", " "))


# Login ------------------------------------------------------------------------


@router.get("")
def root(request: Request) -> Response:
    return RedirectResponse("/odex/home" if current_user(request) else "/odex/login", status_code=303)


@router.get("/login")
def login_page(next_page: str = Query("/odex/home", alias="next"), failed: bool = False) -> Response:
    hint = ""
    if default_login():
        username, password = odex_login()
        hint = f'<p class="hint">Local demo login: <code>{esc(username)}</code> / <code>{esc(password)}</code></p>'
    error = '<p class="error" id="login-error">Wrong username or password.</p>' if failed else ""
    body = f"""<div class="card" style="max-width:400px;margin:32px auto">
<h1>Log in</h1>{error}
<form method="post" action="/odex/login" id="login-form" class="grid" style="grid-template-columns:1fr">
<input type="hidden" name="next" value="{esc(next_page)}">
{pages.field("username", "Username")}
{pages.field("password", "Password", type="password")}
<div class="actions"><button class="btn primary" id="login" type="submit">Log in</button></div>
</form>{hint}</div>"""
    return pages.page("Log in", body, site="odex")


@router.post("/login")
async def login(request: Request) -> Response:
    form = await request.form()
    target = forms.text(form, "next")
    if not target.startswith("/odex/") or target.startswith("/odex/log"):
        target = "/odex/home"
    username, password = odex_login()
    right_user = secrets.compare_digest(forms.text(form, "username").encode(), username.encode())
    right_password = secrets.compare_digest(str(form.get("password") or "").encode(), password.encode())
    if not (right_user and right_password):
        return RedirectResponse(f"/odex/login?failed=1&next={quote(target)}", status_code=303)
    token = secrets.token_urlsafe(24)
    with store_of(request).change() as data:
        data["sessions"][token] = username
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", path="/odex")
    return response


@router.get("/logout")
def logout(request: Request) -> Response:
    token = request.cookies.get(COOKIE)
    if token:
        with store_of(request).change() as data:
            data["sessions"].pop(token, None)
    response = RedirectResponse("/odex/login", status_code=303)
    response.delete_cookie(COOKIE, path="/odex")
    return response


@router.get("/files/{file_id}")
def download(file_id: str, request: Request) -> Response:
    if current_user(request) is None:
        return to_login(request)
    found = store_of(request).file(file_id)
    if found is None:
        return Response("No such file.", status_code=404)
    name, path = found
    # As an attachment, so a browser saves it instead of opening its PDF viewer.
    return FileResponse(path, media_type="application/pdf", filename=name)


# Counter Process → Deposit and Refund -------------------------------------------


@router.get("/counter-process/deposit-refund")
def deposits(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return to_login(request)
    with store_of(request).read() as data:
        items = list(reversed(data["deposits"]))
    rows = [
        _row(d, [
            esc(d["ref"]), esc(d["bl_no"]), pages.money(d["amount"]), esc(d["utr"]), esc(d["refund_to"]),
            pages.status(d["status"]) + (f'<div class="hint">{esc(d["reason"])}</div>' if d.get("reason") else ""),
            _file_link(d.get("slip_file"), "Download slip", "download-slip"), _when(d),
        ])
        for d in items
    ]
    body = f"""<h1>Deposit and Refund</h1><p class="path">Counter Process → Deposit and Refund</p>
<p><a class="btn primary" id="new-deposit" href="{DEPOSITS}/new">New Deposit</a></p>
{pages.table("deposits", ["Ref no.", "B/L no.", "Amount (Rs.)", "UTR", "Refund to", "Status", "Slip", "Submitted"], rows, "No deposits yet.")}
<p class="hint">Once CMA CGM approves a deposit, its Security Deposit Acknowledgement Slip can be downloaded here. Refund requests aren't part of this demo.</p>"""
    return _page("Deposit and Refund", body, user)


_THIRD_PARTY_SCRIPT = """
const refundTo = document.getElementById('refundTo');
const showThirdParty = () => {
  const third = refundTo.value === 'Third Party';
  ['field-thirdPartyName', 'field-nocFile'].forEach((id) => { document.getElementById(id).hidden = !third; });
};
refundTo.addEventListener('change', showThirdParty);
showThirdParty();
"""


@router.get("/counter-process/deposit-refund/new")
def new_deposit_page(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return to_login(request)
    pdf = 'accept="application/pdf,.pdf"'
    body = f"""<h1>New Deposit</h1><p class="path">Counter Process → Deposit and Refund → New Deposit</p>
<div class="card"><form method="post" action="{DEPOSITS}/new" id="deposit-form" class="grid" enctype="multipart/form-data">
{pages.select("liner", "Liner", LINERS)}
{pages.field("blNo", "B/L number", hint="One deposit per B/L: enter each house B/L on its own")}
{pages.field("amount", "Deposit amount (Rs.)", type="number", extra='step="0.01" min="0"')}
<fieldset><legend>Payment</legend>
{pages.select("paymentMode", "Payment mode", PAYMENT_MODES)}
{pages.field("utrNo", "UTR / instrument no.")}
{pages.field("paymentDate", "Payment date", type="date")}
{pages.field("bankName", "Bank name")}
</fieldset>
<fieldset><legend>Refund</legend>
{pages.select("refundTo", "Refund to", REFUND_TO, hint="Third Party when the refund isn't to the consignee who paid")}
{pages.field("thirdPartyName", "Third party name")}
{pages.field("nocFile", "Consignee's NOC (PDF)", type="file", extra=pdf, hint="Needed for a refund to a third party")}
</fieldset>
<fieldset><legend>Attachments</legend>
{pages.field("receiptFile", "Security deposit receipt (PDF)", type="file", extra=pdf, hint="The receipt CMA CGM emailed for the deposit")}
</fieldset>
<div class="actions"><button class="btn primary" id="submit" type="submit">Submit Deposit</button>
<a class="btn" href="{DEPOSITS}">Cancel</a></div>
</form></div>"""
    return _page("New Deposit", body, user, script=_THIRD_PARTY_SCRIPT)


@router.post("/counter-process/deposit-refund/new")
async def create_deposit(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return to_login(request)
    form = await request.form()
    liner, mode, bank = forms.text(form, "liner"), forms.text(form, "paymentMode"), forms.text(form, "bankName")
    refund_to, third_party = forms.text(form, "refundTo"), forms.text(form, "thirdPartyName")
    bl_no, utr = forms.code(forms.text(form, "blNo")), forms.code(forms.text(form, "utrNo"))
    amount, paid_on = forms.amount(forms.text(form, "amount")), forms.past_date(forms.text(form, "paymentDate"))
    problems = []
    if liner not in LINERS:
        problems.append("Choose the liner.")
    if not forms.BL.match(bl_no):
        problems.append("Enter the B/L number.")
    if amount is None:
        problems.append("Enter the deposit amount.")
    if mode not in PAYMENT_MODES:
        problems.append("Choose the payment mode.")
    if not forms.UTR.match(utr):
        problems.append("Enter the UTR or instrument number.")
    if paid_on is None:
        problems.append("Enter the payment date (not in the future).")
    if not bank:
        problems.append("Enter the bank name.")
    if refund_to not in REFUND_TO:
        problems.append("Choose who the refund goes to.")
    to_third_party = refund_to == "Third Party"
    if to_third_party and not third_party:
        problems.append("Enter the third party the refund goes to.")
    noc = await forms.attachment(form, "nocFile", "consignee's NOC", problems, required=to_third_party)
    receipt = await forms.attachment(form, "receiptFile", "security deposit receipt", problems, required=True)
    back = (DEPOSITS, "Back to Deposit and Refund")
    path = "Counter Process → Deposit and Refund → New Deposit"
    store = store_of(request)
    try:
        if problems:
            raise forms.Rejected(*problems)
        with store.change() as data:
            for earlier in data["deposits"]:
                if earlier["bl_no"] == bl_no and earlier["status"] != "Rejected":
                    raise forms.Rejected(f"A deposit is already registered for B/L {bl_no}: {earlier['ref']} ({earlier['status']}).")
            ref = next_ref(data, "SD", 5)
            data["deposits"].append({
                "id": secrets.token_hex(6), "ref": ref, "liner": liner, "bl_no": bl_no, "amount": amount,
                "payment_mode": mode, "utr": utr, "payment_date": paid_on, "bank_name": bank,
                "refund_to": refund_to, "third_party_name": third_party if to_third_party else "",
                "noc_file": store.add_file(data, noc[1], noc[0]) if noc else None,
                "receipt_file": store.add_file(data, receipt[1], receipt[0]) if receipt else None,
                "status": "Pending Approval", "created_at": now(), "by": user,
            })
    except forms.Rejected as rejected:
        return _answer(user, "New Deposit", path, back, problems=rejected.problems)
    return _answer(
        user, "New Deposit", path, back, ref=ref,
        message=f"Deposit {ref} submitted for B/L {bl_no}: Rs. {pages.money(amount or 0)}, UTR {utr}. "
        "It waits for CMA CGM to approve it.",
    )


# e-Payment → DO Request ---------------------------------------------------------


@router.get("/e-payment/do-request")
def do_requests(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return to_login(request)
    with store_of(request).read() as data:
        items = list(reversed(data["do_requests"]))
    rows = [
        _row(r, [
            esc(r["ref"]), esc(r["bl_no"]), esc(r["do_type"]), esc(r["delivery_type"]),
            "<br>".join(esc(d["name"]) for d in r["documents"]),
            pages.status(r["status"]) + (f'<div class="hint">{esc(r["reason"])}</div>' if r.get("reason") else ""),
            _file_link(r.get("edo_file"), "Download eDO", "download-edo"), _when(r),
        ])
        for r in items
    ]
    body = f"""<h1>DO Request</h1><p class="path">e-Payment → DO Request</p>
<p><a class="btn primary" id="new-do-request" href="{DO_REQUESTS}/new">New DO Request</a></p>
{pages.table("do-requests", ["Request no.", "B/L no.", "DO type", "Delivery", "Documents", "Status", "eDO", "Submitted"], rows, "No DO requests yet.")}
<p class="hint">Once CMA CGM issues the delivery order, the eDO can be downloaded here.</p>"""
    return _page("DO Request", body, user)


def _rows_script() -> str:
    options = "".join(f'<option value="{esc(t)}">{esc(t)}</option>' for t in DOC_TYPES)
    return f"""
const docTypes = {json.dumps('<option value="">Select</option>' + options)};
let rowCount = 0;
document.getElementById('addRow').addEventListener('click', () => {{
  rowCount += 1;
  const row = document.createElement('tr');
  row.dataset.row = String(rowCount);
  row.innerHTML = `<td>${{rowCount}}</td>`
    + `<td><select name="docType${{rowCount}}" class="doc-type">${{docTypes}}</select></td>`
    + `<td><input type="file" name="docFile${{rowCount}}" class="doc-file" accept="application/pdf,.pdf"></td>`;
  document.querySelector('#docRows tbody').append(row);
}});
"""


@router.get("/e-payment/do-request/new")
def new_do_request_page(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return to_login(request)
    body = f"""<h1>New DO Request</h1><p class="path">e-Payment → DO Request → New DO Request</p>
<div class="card"><form method="post" action="{DO_REQUESTS}/new" id="do-request-form" class="grid" enctype="multipart/form-data">
{pages.select("liner", "Liner", LINERS)}
{pages.field("blNo", "B/L number", hint="The house B/L (HBL), not the master B/L")}
{pages.select("doType", "DO type", DO_TYPES)}
{pages.select("deliveryType", "Delivery type", DELIVERY_TYPES)}
{pages.field("containers", "Container numbers", hint="Separate several with commas")}
{pages.field("remarks", "Remarks")}
<fieldset><legend>Documents</legend>
<div class="wide"><div class="table-wrap"><table id="docRows"><thead><tr><th>#</th><th>Document type</th><th>File (PDF)</th></tr></thead><tbody></tbody></table></div>
<p><button type="button" class="btn small" id="addRow">Add Row</button></p></div>
</fieldset>
<div class="actions"><button class="btn primary" id="submit" type="submit">Submit DO Request</button>
<a class="btn" href="{DO_REQUESTS}">Cancel</a></div>
</form></div>"""
    return _page("New DO Request", body, user, script=_rows_script())


@router.post("/e-payment/do-request/new")
async def create_do_request(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return to_login(request)
    form = await request.form()
    liner, do_type, delivery = forms.text(form, "liner"), forms.text(form, "doType"), forms.text(form, "deliveryType")
    bl_no, containers = forms.code(forms.text(form, "blNo")), forms.containers(forms.text(form, "containers"))
    problems = []
    if liner not in LINERS:
        problems.append("Choose the liner.")
    if not forms.BL.match(bl_no):
        problems.append("Enter the house B/L number.")
    if do_type not in DO_TYPES:
        problems.append("Choose the DO type.")
    if delivery not in DELIVERY_TYPES:
        problems.append("Choose the delivery type.")
    if bad := [c for c in containers if not forms.CONTAINER.match(c)]:
        problems.append(f"Not a container number: {', '.join(bad)}.")
    documents = []
    rows = sorted(int(k[7:]) for k in form.keys() if k.startswith("docFile") and k[7:].isdigit())
    for row in rows:
        key = f"docFile{row}"
        attached = await forms.attachment(form, key, f"document in row {row}", problems, required=False)
        if attached is None:
            continue
        doc_type = forms.text(form, f"docType{row}")
        if doc_type not in DOC_TYPES:
            problems.append(f"Choose the document type in row {row}.")
        documents.append((doc_type, *attached))
    if not documents and not any("row" in p for p in problems):
        problems.append("Add a row and attach the DO documents.")
    back = (DO_REQUESTS, "Back to DO Request")
    path = "e-Payment → DO Request → New DO Request"
    store = store_of(request)
    try:
        if problems:
            raise forms.Rejected(*problems)
        with store.change() as data:
            for earlier in data["do_requests"]:
                if earlier["bl_no"] == bl_no and earlier["status"] != "Rejected":
                    raise forms.Rejected(f"A DO request for B/L {bl_no} is already raised: {earlier['ref']} ({earlier['status']}).")
            ref = next_ref(data, "DOR", 4)
            data["do_requests"].append({
                "id": secrets.token_hex(6), "ref": ref, "liner": liner, "bl_no": bl_no, "do_type": do_type,
                "delivery_type": delivery, "containers": containers, "remarks": forms.text(form, "remarks"),
                "documents": [
                    {"doc_type": kind, "name": name, "size": len(content), "file": store.add_file(data, content, name)}
                    for kind, name, content in documents
                ],
                "status": "Submitted", "created_at": now(), "by": user,
            })
    except forms.Rejected as rejected:
        return _answer(user, "New DO Request", path, back, problems=rejected.problems)
    files = ", ".join(f"{name} ({len(content):,} bytes)" for _, name, content in documents)
    return _answer(
        user, "New DO Request", path, back, ref=ref,
        message=f"DO request {ref} submitted for B/L {bl_no} with {files}. It waits for CMA CGM to issue the DO.",
    )
