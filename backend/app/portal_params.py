"""What the browser agent types into each portal page, taken from the database.

Seven steps of the CMA CGM process happen on websites (the SOP sheet of DO
PROCESS FLOW E2E.xlsx). For each one this module names the SOP that does it, and
builds that SOP's params from the job's fields, the fixed details and the job's
documents. A step won't start while something it needs is missing, and the
error names everything that is.

    step                        site      runs once per   what the SOP gets
    odex_request_invoice        ODeX      MBL             liner, MBL, containers, emails
    cma_rtgs_shipment_payment   CMA RTGS  MBL             contact details, the payment, invoice no. and MBL
    cma_rtgs_security_deposit   CMA RTGS  MBL             contact details, the payment, MBL as the OBL
    odex_deposit_new            ODeX      house bill      deposit and its UTR, bank, refund, NOC, receipt
    odex_deposit_status         ODeX      house bill      where to save the deposit slip
    odex_do_request_new         ODeX      house bill      HBL, DO and delivery type, folder PDF
    odex_do_request_status      ODeX      house bill      where to save the eDO

sops/local/README.md lists every param with the field it comes from.
"""
from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from . import assemble, containers, letters, service
from .config import BACKEND_DIR
from .db import Profile, Shipment
from .folder import FieldDef, Slot, is_filled, split_list
from .runtime import Runtime

SOPS_DIR = BACKEND_DIR / "sops"

ODEX = "odex"
CMA_RTGS = "cma_rtgs"

REQUEST_INVOICE = "odex_request_invoice"
RTGS_SHIPMENT = "cma_rtgs_shipment_payment"
RTGS_DEPOSIT = "cma_rtgs_security_deposit"
DEPOSIT = "odex_deposit_new"
DEPOSIT_STATUS = "odex_deposit_status"
DO_REQUEST = "odex_do_request_new"
DO_REQUEST_STATUS = "odex_do_request_status"

# Hexalog signs the NOC for refunding the deposit to the CHA.
NOC_STAMP = "hexalog"

# The fields the NOC for the refund prints.
NOC_FIELDS = (
    "letter_date",
    "line_agent",
    "mbl_no",
    "vessel",
    "containers",
    "deposit_amount",
    "cha",
    "forwarder_pan",
    "forwarder_name",
    "forwarder_signatory",
    "forwarder_designation",
)


class MissingData(service.NotReady):
    """A step can't start yet. The message names everything that's missing."""

    def __init__(self, step: PortalStep, missing: list[str]) -> None:
        self.step = step
        self.missing = missing
        super().__init__(f"{step.title} needs {_and(missing)} first.")


class _Job:
    """One job's values, as the portals want them, noting whatever is missing."""

    def __init__(self, rt: Runtime, session: Session, shipment: Shipment, profile: Profile) -> None:
        self.rt = rt
        self.session = session
        self.shipment = shipment
        self.profile = profile
        self.missing: list[str] = []

    def _lack(self, what: str) -> str:
        if what not in self.missing:
            self.missing.append(what)
        return ""

    def field(self, key: str) -> str:
        field = self.rt.folder.fields[key]
        value = self.rt.folder.value(key, self.profile.data, self.shipment.data)
        if not is_filled(field, value):
            return self._lack(f"{field.label} (fixed details)" if field.scope == "profile" else field.label)
        return _as_text(field, value)

    def money(self, key: str) -> str:
        """An amount as CMA CGM's RTGS page shows it: 54720.68, 40000.00."""
        value = self.field(key)
        return f"{float(value):.2f}" if value else ""

    def day_month_year(self, key: str) -> str:
        """A date as CMA CGM's RTGS page takes it: 03-10-2026."""
        value = self.field(key)
        return date.fromisoformat(value).strftime("%d-%m-%Y") if value else ""

    def document(self, doc_key: str) -> str:
        """A PDF copy of a document attached to the DO folder, named for what it is,
        since the portal shows the name of the file it was given."""
        doc = self.rt.folder.doc(doc_key)
        attached = service.attached(self.session, self.shipment, Slot(doc, None)) if doc else None
        if doc is None or attached is None:
            return self._lack(f"the {doc.title if doc else doc_key} in the DO folder")
        copy = Path(self.portal_file(f"{doc.title} {self.shipment.data.get('mbl_no') or ''}".strip() + ".pdf"))
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(assemble.shipment_dir(self.rt.settings, self.shipment.id) / attached.pdf_file, copy)
        return str(copy)

    def folder_pdf(self) -> str:
        if not self.shipment.folder_file:
            return self._lack("a built DO folder")
        if service.stale(self.shipment, self.profile):
            raise service.NotReady("The DO folder changed after it was built. Build it again first.")
        path = service.folder_pdf_path(self.rt, self.shipment)
        if path is None or not path.is_file():
            raise service.NotReady("The built folder PDF is missing. Build the folder again.")
        return str(path)

    def portal_file(self, name: str) -> str:
        """Where a file from a portal is kept: with the job's other files."""
        return str(assemble.shipment_dir(self.rt.settings, self.shipment.id) / "portal" / name)

    def refund_noc(self) -> str:
        """Writes the NOC for refunding the deposit to the CHA, from the current fields."""
        for key in NOC_FIELDS:
            self.field(key)
        if self.missing:
            return ""
        data = assemble.letter_values(self.rt.folder, self.rt.settings, NOC_STAMP, self.profile.data, self.shipment.data)
        path = Path(self.portal_file("noc-deposit-refund.pdf"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(letters.noc_refund(data))
        return str(path)


def _request_invoice(job: _Job, hbl: str | None) -> dict[str, str]:
    return {
        "liner": job.field("odex_liner"),
        # CMA CGM invoices the master bill.
        "mbl_no": job.field("mbl_no"),
        "containers": job.field("containers"),
        # CMA CGM emails the invoice here once it's verified.
        "email": job.field("do_mailbox"),
        "job_no": job.shipment.job_no,
    }


def _rtgs(job: _Job, deposit: bool) -> dict[str, str]:
    """CMA CGM's RTGS Confirmation page, filed once for each payment Finance makes."""
    return {
        # Contact Details: who confirms, and where the receipt is emailed.
        "company_name": job.field("forwarder_name"),
        "contact_name": job.field("rtgs_contact_name"),
        "contact_number": job.field("rtgs_contact_number"),
        "emails": job.field("do_mailbox"),
        # Payment Details: the payment as the bank made it.
        "utr": job.field("deposit_utr" if deposit else "payment_utr"),
        "bank_account": job.field("payer_account_no"),
        "amount": job.money("deposit_amount" if deposit else "do_invoice_amount"),
        "transfer_date": job.day_month_year("payment_date"),
        # Invoice Details, row 1: the DO invoice, or a ticked Security Deposit
        # against the OBL, which is the master bill.
        "security_deposit": "yes" if deposit else "",
        "invoice_no": "" if deposit else job.field("do_invoice_no"),
        "bl_no": job.field("mbl_no"),
    }


def _rtgs_shipment(job: _Job, hbl: str | None) -> dict[str, str]:
    return _rtgs(job, deposit=False)


def _rtgs_deposit(job: _Job, hbl: str | None) -> dict[str, str]:
    return _rtgs(job, deposit=True)


def _deposit(job: _Job, hbl: str | None) -> dict[str, str]:
    refund_to = job.field("deposit_refund_to")
    # A refund to anyone but the consignee who paid needs the consignee's NOC.
    third_party = refund_to == "Third Party"
    params = {
        "liner": job.field("odex_liner"),
        # ODeX takes a deposit per B/L: each house bill gets its own.
        "hbl_no": hbl or "",
        "amount": job.field("deposit_amount"),
        "utr": job.field("deposit_utr"),
        "payment_date": job.field("payment_date"),
        "bank_name": job.field("payer_bank"),
        "refund_to": refund_to,
        "third_party_name": job.field("cha") if third_party else "",
        "receipt_file": job.document("security_receipt"),
        "noc_file": "",
    }
    if third_party:
        params["noc_file"] = job.refund_noc()
    return params


def _deposit_status(job: _Job, hbl: str | None) -> dict[str, str]:
    return {"hbl_no": hbl or "", "slip_save_path": job.portal_file(f"{hbl}-security-deposit-slip.pdf")}


def _do_request(job: _Job, hbl: str | None) -> dict[str, str]:
    return {
        "liner": job.field("odex_liner"),
        # ODeX wants the house bill here, not the master bill.
        "hbl_no": hbl or "",
        "mbl_no": job.field("mbl_no"),
        "job_no": job.shipment.job_no,
        "do_type": job.field("do_type"),
        "delivery_type": job.field("delivery_type"),
        "containers": job.field("containers"),
        # One folder per MBL, uploaded with every house bill's request.
        "folder_pdf_path": job.folder_pdf(),
    }


def _do_request_status(job: _Job, hbl: str | None) -> dict[str, str]:
    return {"hbl_no": hbl or "", "edo_save_path": job.portal_file(f"{hbl}-eDO.pdf")}


@dataclass(frozen=True)
class PortalStep:
    key: str
    title: str  # what the step does, as ops would say it
    site: str
    sop: Path  # the SOP for the local version of the site (make portals)
    per_hbl: bool  # once per house bill, or once per MBL
    reads: tuple[str, ...]  # what the SOP reads back from the page
    build: Callable[[_Job, str | None], dict[str, str]]


def _local(path: str) -> Path:
    return SOPS_DIR / "local" / path


STEPS: dict[str, PortalStep] = {
    step.key: step
    for step in (
        PortalStep(
            REQUEST_INVOICE, "Requesting the DO invoice on ODeX", ODEX,
            _local("odex/request_invoice.yaml"), False, ("odex_invoice_request_ref",), _request_invoice,
        ),
        PortalStep(
            RTGS_SHIPMENT, "Confirming the shipment payment on CMA CGM's RTGS page", CMA_RTGS,
            _local("cma_rtgs/new_request.yaml"), False, ("cma_rtgs_request_ref",), _rtgs_shipment,
        ),
        PortalStep(
            RTGS_DEPOSIT, "Confirming the security deposit on CMA CGM's RTGS page", CMA_RTGS,
            _local("cma_rtgs/new_request.yaml"), False, ("cma_rtgs_request_ref",), _rtgs_deposit,
        ),
        PortalStep(
            DEPOSIT, "Raising the security deposit on ODeX", ODEX,
            _local("odex/deposit_new.yaml"), True, ("odex_deposit_ref",), _deposit,
        ),
        PortalStep(
            DEPOSIT_STATUS, "Checking the deposit on ODeX", ODEX,
            _local("odex/deposit_status.yaml"), True, ("deposit_ref", "deposit_status", "downloaded_file_path"),
            _deposit_status,
        ),
        PortalStep(
            DO_REQUEST, "Raising the DO request on ODeX", ODEX,
            _local("odex/do_request_new.yaml"), True, ("odex_request_ref",), _do_request,
        ),
        PortalStep(
            DO_REQUEST_STATUS, "Checking the DO request on ODeX", ODEX,
            _local("odex/do_request_status.yaml"), True,
            ("do_request_ref", "do_request_status", "downloaded_file_path"), _do_request_status,
        ),
    )
}


def targets(step: PortalStep, shipment: Shipment, rt: Runtime) -> list[str | None]:
    """The house bills a per-HBL step runs for; a single run (None) otherwise."""
    if not step.per_hbl:
        return [None]
    hbls = split_list(shipment.data.get("hbl_nos"))
    if not hbls:
        raise MissingData(step, [rt.folder.fields["hbl_nos"].label])
    return hbls


def params_for(
    rt: Runtime, session: Session, shipment: Shipment, step_key: str, hbl: str | None = None
) -> dict[str, str]:
    """The params for one run of a step. Raises MissingData, or NotReady when the
    folder needs building again."""
    step = STEPS[step_key]
    if step.per_hbl and hbl not in targets(step, shipment, rt):
        raise service.NotReady(f"{hbl} is not one of the house bills of job {shipment.job_no}.")
    job = _Job(rt, session, shipment, service.load_profile(session))
    params = step.build(job, hbl)
    if job.missing:
        raise MissingData(step, job.missing)
    if step.site == ODEX:
        params["odex_url"] = rt.settings.odex_url
    else:
        params["cma_rtgs_url"] = rt.settings.cma_rtgs_url
    return params


def params_job(rt: Runtime, shipment_id: str, step_key: str) -> dict[str, dict[str, str]]:
    """Params for every run of a step: by house bill, or under the MBL for a step
    that runs once."""
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, shipment_id)
        step = STEPS[step_key]
        return {
            hbl or shipment.data.get("mbl_no") or shipment.job_no: params_for(rt, session, shipment, step_key, hbl)
            for hbl in targets(step, shipment, rt)
        }


def _as_text(field: FieldDef, value: Any) -> str:
    if field.type == "containers":
        return ", ".join(containers.split(value))
    if field.type == "list":
        return ", ".join(split_list(value))
    if field.type == "number":
        return _amount(value)
    text = str(value).strip()
    return text.upper() if field.code else text


def _amount(value: Any) -> str:
    """40000, or 54720.68: how the portals' amount boxes take it."""
    number = float(value)
    return f"{number:.0f}" if number.is_integer() else f"{number:.2f}"


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
