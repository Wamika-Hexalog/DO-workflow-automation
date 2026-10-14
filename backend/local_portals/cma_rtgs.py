"""The local version of CMA CGM's RTGS payments page, at the real one's path
(rtgs.india.cma-cgm.com/index.php/Finance/RTGS/NewRequest/) under /cma-rtgs.

Once Finance has paid the DO invoice and the security deposit, ops enter both
payments here against the B/L, and CMA CGM emails a receipt for each to the
address given (the control panel plays that part). The real page's fields aren't
known yet: these are the ones its two receipts print.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from . import forms, pages
from .store import next_ref, now

router = APIRouter(prefix="/cma-rtgs")

NEW_REQUEST = "/cma-rtgs/index.php/Finance/RTGS/NewRequest/"
PATH = "Finance → RTGS → New Request"


@router.get("")
def root() -> Response:
    return RedirectResponse(NEW_REQUEST, status_code=303)


@router.get("/index.php/Finance/RTGS/NewRequest/")
def new_request_page() -> Response:
    money = 'step="0.01" min="0"'
    body = f"""<h1>New RTGS Request</h1><p class="path">{PATH}</p>
<div class="card"><form method="post" action="{NEW_REQUEST}" id="rtgs-form" class="grid">
{pages.field("blNo", "B/L number")}
{pages.field("customerName", "Customer name", hint="Who paid: the consignee on the B/L")}
{pages.field("email", "Email for the receipts", type="email", hint="CMA CGM emails the receipts here")}
{pages.field("bankName", "Remitting bank")}
{pages.field("paymentDate", "Payment date", type="date")}
<fieldset><legend>DO invoice payment</legend>
{pages.field("invoiceNo", "Invoice number")}
{pages.field("invoiceAmount", "Amount (Rs.)", type="number", extra=money)}
{pages.field("invoiceUtr", "UTR number")}
</fieldset>
<fieldset><legend>Security deposit payment</legend>
{pages.field("depositAmount", "Amount (Rs.)", type="number", extra=money)}
{pages.field("depositUtr", "UTR number")}
</fieldset>
<div class="actions"><button class="btn primary" id="submit" type="submit">Submit</button></div>
</form></div>
<p class="hint">Enter each payment with the UTR the bank gave it. A receipt for each is emailed once CMA CGM's finance team finds the payment.</p>"""
    return pages.page("New RTGS Request", body, site="cma_rtgs")


@router.post("/index.php/Finance/RTGS/NewRequest/")
async def create_request(request: Request) -> Response:
    form = await request.form()
    bl_no, customer, email, bank = forms.code(forms.text(form, "blNo")), forms.text(form, "customerName"), forms.text(form, "email"), forms.text(form, "bankName")
    paid_on = forms.past_date(forms.text(form, "paymentDate"))
    invoice_no = forms.code(forms.text(form, "invoiceNo"))
    invoice_amount, invoice_utr = forms.amount(forms.text(form, "invoiceAmount")), forms.code(forms.text(form, "invoiceUtr"))
    deposit_amount, deposit_utr = forms.amount(forms.text(form, "depositAmount")), forms.code(forms.text(form, "depositUtr"))
    problems = []
    if not forms.BL.match(bl_no):
        problems.append("Enter the B/L number.")
    if not customer:
        problems.append("Enter the customer name.")
    if not forms.EMAIL.match(email):
        problems.append("Enter the email address for the receipts.")
    if not bank:
        problems.append("Enter the remitting bank.")
    if paid_on is None:
        problems.append("Enter the payment date (not in the future).")
    invoice_part = any((invoice_no, forms.text(form, "invoiceAmount"), invoice_utr))
    deposit_part = any((forms.text(form, "depositAmount"), deposit_utr))
    if not invoice_part and not deposit_part:
        problems.append("Enter at least one payment: the DO invoice or the security deposit.")
    if invoice_part:
        if not forms.INVOICE_NO.match(invoice_no):
            problems.append("Enter the invoice number.")
        if invoice_amount is None:
            problems.append("Enter the invoice amount.")
        if not forms.UTR.match(invoice_utr):
            problems.append("Enter the UTR of the invoice payment.")
    if deposit_part:
        if deposit_amount is None:
            problems.append("Enter the deposit amount.")
        if not forms.UTR.match(deposit_utr):
            problems.append("Enter the UTR of the deposit payment.")
    try:
        if problems:
            raise forms.Rejected(*problems)
        with request.app.state.store.change() as data:
            entered = {u for u in (invoice_utr if invoice_part else "", deposit_utr if deposit_part else "") if u}
            for earlier in data["rtgs_requests"]:
                reused = entered & {earlier["invoice_utr"], earlier["deposit_utr"]}
                if reused and earlier["status"] != "Rejected":
                    raise forms.Rejected(f"UTR {', '.join(sorted(reused))} is already entered, on request {earlier['ref']}.")
            ref = next_ref(data, "RTGS", 4)
            data["rtgs_requests"].append({
                "id": secrets.token_hex(6), "ref": ref, "bl_no": bl_no, "customer_name": customer, "email": email,
                "bank_name": bank, "payment_date": paid_on,
                "invoice_no": invoice_no if invoice_part else "",
                "invoice_amount": invoice_amount if invoice_part else None,
                "invoice_utr": invoice_utr if invoice_part else "",
                "deposit_amount": deposit_amount if deposit_part else None,
                "deposit_utr": deposit_utr if deposit_part else "",
                "status": "Received", "created_at": now(),
            })
    except forms.Rejected as rejected:
        body = (
            f'<h1>New RTGS Request</h1><p class="path">{PATH}</p>'
            f"{pages.outcome(False, 'The request was not submitted.', problems=rejected.problems)}"
            f'<p><a class="btn" id="back" href="{NEW_REQUEST}">Back</a></p>'
        )
        return pages.page("New RTGS Request", body, site="cma_rtgs", status_code=400)
    message = (
        f"RTGS request {ref} received for B/L {bl_no}. CMA CGM emails the receipts to {email} once its finance "
        "team finds the payments."
    )
    body = (
        f'<h1>New RTGS Request</h1><p class="path">{PATH}</p>{pages.outcome(True, message, reference=ref)}'
        f'<p><a class="btn" id="back" href="{NEW_REQUEST}">Enter another payment</a></p>'
    )
    return pages.page("New RTGS Request", body, site="cma_rtgs")

