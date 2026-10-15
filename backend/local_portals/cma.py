"""CMA CGM's side of the process, which the control panel plays: verifying the DO
invoice, confirming the RTGS payments with receipts, approving the security
deposit and issuing the eDO. For real each takes hours or days; here it takes a
click, or a few seconds with autoplay on.

Each answer moves one waiting request on and writes the documents CMA CGM would
send. What would arrive by email goes in the outbox on the control panel, written
like the real mail (ODeX's invoices received, the RTGS confirmation, the security
slip); where no real mail has been seen (the eDO issued) the mail carries a code line.
local_portals/mail.py hands the outbox to the app when the demo asks for it.
"""
from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from . import documents, mail
from .settings import ACCOUNT_NAME
from .store import Store, next_number, now

logger = logging.getLogger(__name__)

# The DO invoice: per container and per B/L, plus GST. One container comes to
# Rs. 54,720.68, as on the sample DO receipt.
TARIFF = (
    ("Terminal handling charges", "per container", 33873.46),
    ("Delivery order fee", "per B/L", 7500.00),
    ("Documentation fee", "per B/L", 5000.00),
)
GST_RATE = 0.18
EDO_VALID_DAYS = 14

# Each kind of request, and the status in which it waits for CMA CGM.
WAITING = {
    "invoice_requests": "Pending",
    "rtgs_requests": "Received",
    "deposits": "Pending Approval",
    "do_requests": "Submitted",
}


class NotWaiting(Exception):
    """The request was answered already, or doesn't exist."""


def _waiting(data: dict[str, Any], kind: str, item_id: str) -> dict[str, Any]:
    for item in data[kind]:
        if item["id"] == item_id:
            if item["status"] != WAITING[kind]:
                raise NotWaiting(f"{item['ref']} is {item['status']} already.")
            return item
    raise NotWaiting(f"There is no request {item_id}.")


DO_MAILBOX = "the DO mailbox"
ODEX_ACCOUNT = "the ODeX account's email"


def _email(
    data: dict[str, Any], to: str, subject: str, body: str, attachments: list[str],
    codes: list[tuple[str, str, str]] = (),
) -> None:
    """``codes``: (stage, B/L, argument) for the code lines the app reads (app/email_code.py)."""
    data["outbox"].insert(0, {
        "id": secrets.token_hex(6), "at": now(), "to": to, "subject": subject, "body": body,
        "attachments": attachments, "codes": [list(code) for code in codes],
    })


def verify_invoice(store: Store, item_id: str) -> str:
    """The liner checks the invoice request and releases its DO invoice on ODeX,
    where ops download it (BL Status → the request → its attachment)."""
    with store.change() as data:
        request = _waiting(data, "invoice_requests", item_id)
        count = max(1, len(request.get("containers") or []))
        lines = [
            (text, f"{count} x {documents.money(rate)}", round(rate * count, 2))
            if basis == "per container"
            else (text, basis, rate)
            for text, basis, rate in TARIFF
        ]
        subtotal = round(sum(amount for *_, amount in lines), 2)
        gst = round(subtotal * GST_RATE, 2)
        total = round(subtotal + gst, 2)
        invoice_no = f"INV-DEMO-{next_number(data, 'invoice'):05d}"
        pdf = documents.invoice_pdf(
            invoice_no=invoice_no, on=date.today().isoformat(), liner=request["liner"], bl_no=request["bl_no"],
            containers=request.get("containers") or [], request_ref=request["ref"], lines=lines, gst=gst, total=total,
        )
        file_id = store.add_file(data, pdf, f"{invoice_no}.pdf")
        request.update(
            status="Released", invoice_no=invoice_no, amount=total, invoice_file=file_id,
            invoice_date=date.today().isoformat(), answered_at=now(),
        )
        # As ODeX's notice reads (app/email_infer.py knows it). The bill-to party is the
        # consignee manifested on the MBL.
        _email(
            data, ODEX_ACCOUNT, f"ODeX: Invoices Received for {request['bl_no']}",
            "Dear Sir/Madam,\n\n"
            f"Invoices released for {request['bl_no']},\n"
            f"Type Of Delivery:   {request.get('delivery_type', '')}\n"
            "IGM No.:\nIGM Date:\nCFS Name:\n\n"
            "Invoice No.\tInvoice Type\tTotal Invoice Amt.\tBill to Party\n"
            f"{invoice_no}\tNON FREIGHT\t{total:.2f}\tHEXALOG GLOBAL SUPPLY CHAINS PVT LTD\n\n"
            "Note:This is a system generated e-mail. Please do NOT reply to it.\n\n"
            "Above notification on behalf of CMA CGM AGENCIES INDIA PRIVATE LIMITED\n\n"
            "Regards,\nODeX Support Team\n",
            [],
        )
    return f"Released {request['ref']}: invoice {invoice_no} for Rs. {documents.money(total)}, on ODeX for BL {request['bl_no']}."


def send_receipts(store: Store, item_id: str) -> str:
    """CMA CGM's finance finds the payment and emails its receipt: a Security Receipt
    when it pays only security deposits, a DO Receipt when it pays invoices."""
    with store.change() as data:
        request = _waiting(data, "rtgs_requests", item_id)
        rows = request["invoices"]
        kind = "Security Receipt" if rows and all(r["security_deposit"] for r in rows) else "DO Receipt"
        lines = [(r["invoice_no"], r["bl_no"], r["amount"]) for r in rows]
        if request["advance_amount"]:
            lines.append(("Advance", "-", request["advance_amount"]))
        receipt_no = f"RCPT-DEMO-{next_number(data, 'receipt'):05d}"
        pdf = documents.receipt_pdf(
            kind=kind, receipt_no=receipt_no, on=date.today().isoformat(), customer=request["company_name"],
            lines=lines, total=request["amount"], utr=request["utr"], paid_on=request["transfer_date"],
            account=request["bank_account"],
        )
        file_id = store.add_file(data, pdf, f"{kind} {receipt_no}.pdf")
        request.update(status="Receipt Sent", receipts=[file_id], answered_at=now())
        # As CMA CGM's eBusiness mail reads (app/email_infer.py knows it).
        row = rows[0] if rows else {"invoice_no": "", "bl_no": request["bl_no"], "amount": request["amount"]}
        _email(
            data,
            ", ".join(request["emails"]),
            f"RTGS/NEFT Confirmation Request Processed - {request['ref']}",
            "CMA CGM eBusiness\n"
            f"RTGS/NEFT Confirmation Request Processed - {request['ref']}\n"
            "Status:  Request Confirmed\n"
            "Request Details\n"
            f"Request Id: {request['ref']}\n"
            f"UTR: {request['utr']}\n"
            f"Date of Transfer: {date.fromisoformat(request['transfer_date']):%d-%b-%Y}\n"
            f"Amount Transferred: INR {documents.money(request['amount'])}\n"
            "Invoice Details\n"
            "Invoice Ref. BL Number I/E Amount\n"
            f"{row['invoice_no'] or '-'}\t{row['bl_no']}\tI\t{documents.money(row['amount'])}\n",
            [file_id],
        )
    return f"Emailed {kind} {receipt_no} for {request['ref']} to {', '.join(request['emails'])}."


def approve_deposit(store: Store, item_id: str) -> str:
    """CMA CGM checks the deposit against its receipt; ODeX then offers the slip."""
    with store.change() as data:
        deposit = _waiting(data, "deposits", item_id)
        pdf = documents.deposit_slip_pdf(
            ref=deposit["ref"], on=date.today().isoformat(), account=ACCOUNT_NAME, bl_no=deposit["bl_no"],
            amount=deposit["amount"], utr=deposit["utr"], paid_on=deposit["payment_date"], bank=deposit["bank_name"],
        )
        file_id = store.add_file(data, pdf, f"Security deposit slip {deposit['bl_no']}.pdf")
        deposit.update(status="Deposit Approved", slip_file=file_id, answered_at=now())
        # As ODeX's mail reads (app/email_infer.py knows it).
        _email(
            data, DO_MAILBOX, f"ODeX: Security slip - {deposit['ref']}",
            "Please find the attached security slip issued by CMA CGM AGENCIES INDIA PRIVATE LIMITED\n"
            f"BL No. - {deposit['bl_no']}\n"
            f"Instrument No. - {deposit['utr']}\n"
            "Status - Deposit Approved\n",
            [file_id],
        )
    return f"Approved {deposit['ref']} for B/L {deposit['bl_no']}. ODeX now offers its slip."


def issue_edo(store: Store, item_id: str) -> str:
    """CMA CGM releases the delivery order; ODeX then offers the eDO."""
    with store.change() as data:
        request = _waiting(data, "do_requests", item_id)
        edo_no = f"EDO-DEMO-{next_number(data, 'edo'):05d}"
        today = date.today()
        pdf = documents.edo_pdf(
            edo_no=edo_no, on=today.isoformat(), valid_till=(today + timedelta(days=EDO_VALID_DAYS)).isoformat(),
            liner=request["liner"], account=ACCOUNT_NAME, bl_no=request["bl_no"], containers=request.get("containers") or [],
            do_type=request.get("do_type", "E-DO"), delivery_type=request.get("destuff_type") or request.get("delivery_type", ""),
            request_ref=request["ref"],
        )
        file_id = store.add_file(data, pdf, f"eDO {edo_no} {request['bl_no']}.pdf")
        request.update(status="DO Issued", edo_no=edo_no, edo_file=file_id, answered_at=now())
        _email(
            data, DO_MAILBOX, f"eDO {edo_no} issued for BL {request['bl_no']}",
            f"The delivery order {edo_no} for BL {request['bl_no']} is issued. It is attached, and on ODeX "
            "under DO Request.",
            [file_id], codes=[("edo", request["bl_no"], "")],
        )
    return f"Issued {edo_no} for {request['ref']} (B/L {request['bl_no']}). ODeX now offers the eDO."


def reject(store: Store, kind: str, item_id: str, reason: str) -> str:
    with store.change() as data:
        item = _waiting(data, kind, item_id)
        item.update(status="Rejected", reason=reason.strip() or "Rejected by CMA CGM.", answered_at=now())
    return f"Rejected {item['ref']}: {item['reason']}"


ANSWERS: dict[str, Callable[[Store, str], str]] = {
    "invoice_requests": verify_invoice,
    "rtgs_requests": send_receipts,
    "deposits": approve_deposit,
    "do_requests": issue_edo,
}


class Autoplay:
    """While on, answers each waiting request by itself a few seconds after it
    arrives, so the whole process runs with nobody at the control panel."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="local-portals-autoplay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.tick()
            except Exception:
                logger.exception("Autoplay could not answer a request")

    def tick(self, moment: datetime | None = None) -> list[str]:
        """Answers every request, and every payment mail from the app (as Finance), that
        has waited long enough, then hands the new mail to the app. Returns what it did."""
        moment = moment or datetime.now()
        with self.store.read() as data:
            if not data["autoplay"]["on"]:
                return []
            delay = timedelta(seconds=data["autoplay"]["delay_seconds"])
            due = [
                (kind, item["id"])
                for kind, waiting in WAITING.items()
                for item in data[kind]
                if item["status"] == waiting and datetime.fromisoformat(item["created_at"]) + delay <= moment
            ]
        to_pay = [
            m["id"] for m in mail.finance_mails(self.store)
            if not m["reply"] and datetime.fromtimestamp(m["at"]) + delay <= moment
        ]
        done = []
        for kind, item_id in due:
            try:
                done.append(ANSWERS[kind](self.store, item_id))
            except NotWaiting:
                pass  # answered from the control panel in the meantime
        done += [mail.pay(self.store, message_id) for message_id in to_pay]
        mail.deliver_pending(self.store)
        return done
