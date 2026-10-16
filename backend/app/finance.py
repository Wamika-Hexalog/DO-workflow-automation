"""The finance page: each job's DO invoice from CMA CGM, read from the PDF the browser
agent collected from ODeX (invoice_reader.py), with where its payment and its security
deposit stand. The page adds them up across the jobs and filters them.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select

from . import invoice_reader, portal_params, tasks
from .db import Shipment
from .folder import STAGE_IDS, split_list
from .runtime import Runtime

UNPAID, PAID, CONFIRMED, APPROVED = "unpaid", "paid", "confirmed", "approved"


@lru_cache(maxsize=512)
def _read(path: str, modified: int) -> dict[str, Any]:
    """Each version of an invoice file is read once."""
    return invoice_reader.read(Path(path).read_bytes())


def jobs_job(rt: Runtime) -> list[dict[str, Any]]:
    """Every job, its invoice (None until the agent collects it) and its payments."""
    rows = []
    with rt.sessionmaker() as session:
        for shipment in session.scalars(select(Shipment).order_by(Shipment.job_no)):
            data = shipment.data
            path = Path(portal_params.invoice_pdf_path(rt, shipment))
            invoice, problem = None, None
            if path.is_file():
                try:
                    invoice = _read(str(path), path.stat().st_mtime_ns)
                except invoice_reader.UnreadableInvoice as exc:
                    problem = str(exc)
            stage = STAGE_IDS.index(shipment.status)
            confirmed = tasks.get(session, shipment.id, tasks.CMA_RTGS_PAYMENT)
            payment = UNPAID
            if data.get("payment_utr"):
                done = confirmed is not None and confirmed.status == tasks.COMPLETED
                payment = CONFIRMED if done or stage >= STAGE_IDS.index("invoice_paid") else PAID
            deposit = UNPAID
            if data.get("deposit_utr"):
                deposit = APPROVED if stage >= STAGE_IDS.index("deposit_approved") else PAID
            rows.append({
                "shipment_id": shipment.id,
                "job_no": shipment.job_no,
                "mbl": data.get("mbl_no") or "",
                "hbls": split_list(data.get("hbl_nos")),
                "stage": shipment.status,
                "owner": shipment.owner,
                "invoice": invoice,
                "invoice_url": f"/api/shipments/{shipment.id}/invoice.pdf" if path.is_file() else None,
                "invoice_problem": problem,
                "payment": {"status": payment, "utr": _last4(data.get("payment_utr")), "date": data.get("payment_date")},
                "deposit": {
                    "status": deposit, "amount": _amount(data.get("deposit_amount")),
                    "utr": _last4(data.get("deposit_utr")),
                },
            })
    return rows


def _last4(utr: Any) -> str | None:
    return f"…{str(utr)[-4:]}" if utr else None


def _amount(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else None
    except ValueError:
        return None
