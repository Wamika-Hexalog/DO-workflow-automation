"""The portal steps around the DO request, as the workflows run them
(app/workflows/invoice.py, payment.py and edo.py): when each one may run, what it
leaves on the job, and when the payments go in front of ops.

The agent runs a step by itself once its mail arrives. Ops approve the payment
steps first, because they confirm or move money. Nothing here runs while
DO_AUTOMATE_PORTALS is off.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from . import portal_params, service, tasks
from .folder import STAGE_IDS, Slot, split_list
from .runtime import Runtime

AGENT = "browser agent"

TASK_OF = {
    portal_params.REQUEST_INVOICE: tasks.ODEX_INVOICE_REQUEST,
    portal_params.INVOICE_FETCH: tasks.ODEX_INVOICE_COLLECT,
    portal_params.RTGS_SHIPMENT: tasks.CMA_RTGS_PAYMENT,
    portal_params.RTGS_DEPOSIT: tasks.CMA_RTGS_DEPOSIT,
    portal_params.DEPOSIT: tasks.ODEX_DEPOSIT,
    portal_params.DO_REQUEST_STATUS: tasks.ODEX_EDO,
}
# The steps ops approve, in the order the process reaches them.
FOR_OPS = (portal_params.RTGS_SHIPMENT, portal_params.RTGS_DEPOSIT, portal_params.DEPOSIT)

INVOICE_PAID, DEPOSIT_APPROVED, EDO_RECEIVED = "invoice_paid", "deposit_approved", "edo_received"


def _forward(session: Any, shipment: Any, stage: str) -> bool:
    """Moves the job on to ``stage``, never back."""
    if STAGE_IDS.index(shipment.status) >= STAGE_IDS.index(stage):
        return False
    service.move_to(session, shipment, stage)
    return True


def _last4(utr: str) -> str:
    return f"…{utr[-4:]}" if utr else "-"


def _money(value: Any) -> str:
    return f"{float(value):,.2f}"


def start_job(rt: Runtime, shipment_id: str, task_key: str, retry: bool) -> dict[str, Any]:
    """Whether an automatic step runs now: once its mail arrives, and again when ops
    retry it after a failure. A step that is done, or failed and not retried, waits."""
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, shipment_id)
        if not rt.settings.automate_portals:
            return {"run": False, "why": "DO_AUTOMATE_PORTALS is off."}
        task = tasks.ensure(session, shipment.id, task_key)
        if task.status in (tasks.COMPLETED, tasks.FAILED) and not retry:
            return {"run": False, "why": f"{tasks.TITLES[task_key]} is {task.status}."}
        task.status = tasks.PENDING
        task.note = f"The browser agent is on it: {tasks.TITLES[task_key].lower()}."
        task.completed_at = task.completed_by = None
        session.commit()
        return {"run": True}


def _outcome(step_key: str, shipment: Any, readings: dict[str, dict[str, Any]]) -> tuple[bool, str, str | None]:
    """Whether the step is done, what to tell ops, and the stage it moves the job to."""
    first = next(iter(readings.values()), {})
    if step_key == portal_params.REQUEST_INVOICE:
        return True, f"Requested the DO invoice on ODeX: request {first.get('odex_invoice_request_ref') or '-'}.", None
    if step_key == portal_params.INVOICE_FETCH:
        if not first.get("invoice_no"):
            status = first.get("invoice_status") or "pending"
            return False, f"The liner hasn't released the DO invoice on ODeX yet ({status}).", None
        return True, f"Collected DO invoice {first['invoice_no']}, Rs. {first.get('invoice_amount')}, from ODeX.", None
    if step_key == portal_params.RTGS_SHIPMENT:
        ref = first.get("cma_rtgs_request_ref") or "-"
        return True, f"Confirmed the shipment payment on CMA CGM's RTGS page: request {ref}.", INVOICE_PAID
    if step_key == portal_params.RTGS_DEPOSIT:
        ref = first.get("cma_rtgs_request_ref") or "-"
        return True, f"Confirmed the security deposit on CMA CGM's RTGS page: request {ref}.", None
    if step_key == portal_params.DEPOSIT:
        refs = ", ".join(f"HBL {hbl} {r.get('odex_deposit_ref') or '-'}" for hbl, r in readings.items())
        return True, f"Raised the security deposit on ODeX: {refs}.", None
    # The eDO: done once every house bill has one.
    issued = [hbl for hbl, r in readings.items() if r.get("downloaded_file_path")]
    waiting = [hbl for hbl in split_list(shipment.data.get("hbl_nos")) if hbl not in issued]
    if waiting:
        got = f"Collected the eDO for HBL {', '.join(issued)}. " if issued else ""
        return False, f"{got}Waiting for CMA CGM to issue the eDO for HBL {', '.join(waiting)}.", None
    return True, f"Collected the eDO for HBL {', '.join(issued)} from ODeX.", EDO_RECEIVED


def record_job(rt: Runtime, shipment_id: str, step_key: str, readings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Puts what a step read on the job, logs it, and closes its task: done, or
    waiting when the portal hasn't got there yet."""
    for reading in readings.values():
        portal_params.save_readings(rt, shipment_id, step_key, reading)
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, shipment_id)
        finished, note, stage = _outcome(step_key, shipment, readings)
        task_key = TASK_OF[step_key]
        if finished:
            tasks.done(session, shipment.id, task_key, note, {"readings": readings}, by=AGENT)
        else:
            tasks.set_waiting(session, shipment.id, task_key, note)
        service.log(session, shipment.id, "portal", note)
        moved = finished and stage is not None and _forward(session, shipment, stage)
        if moved and stage == EDO_RECEIVED and shipment.edo_received_on is None:
            shipment.edo_received_on = date.today()
        session.commit()
        return {"finished": finished, "moved": moved, "readings": readings}


def _approval_note(step_key: str, params: list[dict[str, str]], hbls: list[str]) -> str:
    p = params[0]
    if step_key == portal_params.RTGS_SHIPMENT:
        return (
            f"Approve to confirm the shipment payment on CMA CGM's RTGS page: invoice {p['invoice_no']}, "
            f"Rs. {p['amount']}, UTR {_last4(p['utr'])}, transferred {p['transfer_date']}."
        )
    if step_key == portal_params.RTGS_DEPOSIT:
        return (
            f"Approve to confirm the security deposit on CMA CGM's RTGS page: Rs. {p['amount']}, "
            f"UTR {_last4(p['utr'])}, transferred {p['transfer_date']}."
        )
    return (
        f"Approve to raise the security deposit on ODeX for HBL {', '.join(hbls)}: Rs. {p['amount']} each, "
        f"UTR {_last4(p['utr'])}, CMA iReceipt no. {p['ireceipt_no']}, refunded to {p['refund_party']}."
    )


def _slips_in(rt: Runtime, session: Any, shipment: Any) -> bool:
    """ODeX's security slip is in for every house bill."""
    doc = rt.folder.doc("security_odex_receipt")
    hbls = split_list(shipment.data.get("hbl_nos"))
    return bool(doc and hbls) and all(service.attached(session, shipment, Slot(doc, hbl)) for hbl in hbls)


def refresh_payments_job(rt: Runtime, shipment_id: str) -> dict[str, Any]:
    """Puts each payment step in front of ops once the job has what it needs, and
    moves the job to Deposit approved once ODeX's slip is in for every house bill."""
    if not rt.settings.automate_portals:
        return {"ready": [], "deposit_approved": False}
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, shipment_id)
        ready = []
        for step_key in FOR_OPS:
            task_key = TASK_OF[step_key]
            task = tasks.get(session, shipment.id, task_key)
            if task is not None and task.status in (tasks.COMPLETED, tasks.FAILED):
                continue
            step = portal_params.STEPS[step_key]
            try:
                hbls = [hbl for hbl in portal_params.targets(step, shipment, rt) if hbl]
                params = [portal_params.params_for(rt, session, shipment, step_key, hbl) for hbl in (hbls or [None])]
            except service.NotReady as exc:
                tasks.set_waiting(session, shipment.id, task_key, str(exc))
                continue
            note = _approval_note(step_key, params, hbls)
            before = task.status if task is not None else None
            if tasks.signal_ready(session, shipment.id, task_key, note) and before != tasks.AWAITING_APPROVAL:
                service.log(session, shipment.id, "task", note)
                ready.append(task_key)
        approved = _slips_in(rt, session, shipment) and _forward(session, shipment, DEPOSIT_APPROVED)
        session.commit()
        return {"ready": ready, "deposit_approved": approved}


# The mail to Finance -------------------------------------------------------------


def _addresses(value: Any) -> list[str]:
    """Addresses from a fixed detail, comma-separated."""
    return [a.strip() for a in str(value or "").split(",") if a.strip()]


def finance_mail_job(rt: Runtime, shipment_id: str) -> dict[str, Any]:
    """Asks Finance to pay CMA CGM, as Customs Ops ask it ("<job> // <MBL> // CMA //"):
    the shipping line invoice attached, and the security deposit. One line more asks
    for the two UTRs in a shape app/email_infer.py reads; the marker at the end of
    the subject brings the reply in."""
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, shipment_id)
        profile = service.load_profile(session)
        to, cc = _addresses(profile.data.get("finance_email")), _addresses(profile.data.get("mail_cc"))
        mbl = shipment.data.get("mbl_no") or ""
        invoice_no = shipment.data.get("do_invoice_no") or ""
        amount = shipment.data.get("do_invoice_amount")
        deposit = shipment.data.get("deposit_amount")
        pdf = Path(portal_params.invoice_pdf_path(rt, shipment))
        missing = [
            what for what, have in (
                ("Finance's email in the fixed details", to), ("the DO invoice no.", invoice_no),
                ("the DO invoice amount", amount), ("the invoice PDF from ODeX", pdf.is_file()),
            ) if not have
        ]
        if missing:
            raise service.NotReady(f"The mail to Finance needs {', '.join(missing)} first.")
        security = (
            f"<p>Also please arrange Security Amount of Rs. {float(deposit):,.0f}/-</p>" if deposit else ""
        )
        body = (
            "<p>Dear Finance team,</p>"
            "<p>PFA Shipping line invoice</p>"
            "<p>Kindly arrange payment &amp; share UTR Details</p>"
            f"{security}"
            "<p>Please share the UTRs on these lines:<br>Invoice UTR: <br>Deposit UTR: </p>"
            "<p>Thanks &amp; Regards,<br>Customs Operations</p>"
        )
        subject = f"{shipment.job_no} // {mbl} // CMA // {rt.settings.mail_marker}".strip()
        return {"to": to, "cc": cc, "subject": subject, "body_html": body, "attachments": [str(pdf)]}


def record_finance_mail_job(rt: Runtime, shipment_id: str, sent: dict[str, Any]) -> None:
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, shipment_id)
        copied = f", cc {', '.join(sent['cc'])}" if sent.get("cc") else ""
        note = f"Mailed the DO invoice to Finance ({', '.join(sent['to'])}{copied}), asking for the UTRs."
        tasks.done(session, shipment.id, tasks.FINANCE_MAIL, note, {"message_id": sent.get("id")}, by=AGENT)
        service.log(session, shipment.id, "mail", note)
        session.commit()
