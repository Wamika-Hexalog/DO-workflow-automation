"""What the browser agent types into each portal page, taken from the database.

Eight steps of the CMA CGM process happen on websites (the SOP sheet of DO
PROCESS FLOW E2E.xlsx, and the team's ODeX guide for the invoice). For each one this module names the SOP that does it, and
builds that SOP's params from the job's fields, the fixed details and the job's
documents. A step won't start while something it needs is missing, and the
error names everything that is.

    step                        site      runs once per   what the SOP gets
    odex_request_invoice        ODeX      MBL             location, liner, MBL, delivery type, the MBL's PDF
    odex_invoice_fetch          ODeX      MBL             location, MBL, where to save the invoice
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
INVOICE_FETCH = "odex_invoice_fetch"
RTGS_SHIPMENT = "cma_rtgs_shipment_payment"
RTGS_DEPOSIT = "cma_rtgs_security_deposit"
DEPOSIT = "odex_deposit_new"
DEPOSIT_STATUS = "odex_deposit_status"
DO_REQUEST = "odex_do_request_new"
DO_REQUEST_STATUS = "odex_do_request_status"

# Hexalog signs the NOC for refunding the deposit to the CHA.
NOC_STAMP = "hexalog"

# ODeX's words for the job's delivery types: on the invoice request, and as the DO
# request's DE Stuffing Type (LCL is de-stuffed at the CFS).
ODEX_DELIVERY = {"Factory De-stuff": "Factory Destuff", "CFS De-stuff": "CFS Destuff"}
ODEX_DESTUFF = {"Factory De-stuff": "Factory", "CFS De-stuff": "CFS", "LCL": "CFS", "DPD": "DPD"}
# The endorsements that make the MBL a seaway or telex BL, for the DO request.
SEAWAY_ENDORSEMENTS = ("Waybill", "TELEX Release")

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

    def optional(self, key: str) -> str:
        """A field that may be left empty."""
        field = self.rt.folder.fields[key]
        value = self.rt.folder.value(key, self.profile.data, self.shipment.data)
        return _as_text(field, value) if is_filled(field, value) else ""

    def money(self, key: str) -> str:
        """An amount as CMA CGM's RTGS page shows it: 54720.68, 40000.00."""
        value = self.field(key)
        return f"{float(value):.2f}" if value else ""

    def day_month_year(self, key: str) -> str:
        """A date as CMA CGM's RTGS page takes it: 03-10-2026."""
        value = self.field(key)
        return date.fromisoformat(value).strftime("%d-%m-%Y") if value else ""

    def document(self, doc_key: str, name: str = "") -> str:
        """A PDF copy of a document attached to the DO folder, named for what it is,
        since the portal shows the name of the file it was given."""
        doc = self.rt.folder.doc(doc_key)
        attached = service.attached(self.session, self.shipment, Slot(doc, None)) if doc else None
        if doc is None or attached is None:
            return self._lack(f"the {doc.title if doc else doc_key} in the DO folder")
        copy = Path(self.portal_file(name or f"{doc.title} {self.shipment.data.get('mbl_no') or ''}".strip() + ".pdf"))
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(assemble.shipment_dir(self.rt.settings, self.shipment.id) / attached.pdf_file, copy)
        return str(copy)

    def folder_pdf(self, name: str) -> str:
        """A copy of the built DO folder, named as the portal should show it."""
        if not self.shipment.folder_file:
            return self._lack("a built DO folder")
        if service.stale(self.shipment, self.profile):
            raise service.NotReady("The DO folder changed after it was built. Build it again first.")
        path = service.folder_pdf_path(self.rt, self.shipment)
        if path is None or not path.is_file():
            raise service.NotReady("The built folder PDF is missing. Build the folder again.")
        copy = Path(self.portal_file(name))
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copy)
        return str(copy)

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
    """The team's ODeX guide, steps 1-5. "Invoice In Name of" is ODeX's own, the same every time."""
    mbl = job.field("mbl_no")
    delivery = job.field("delivery_type")
    return {
        # ePayment is opened for the MBL's place of delivery.
        "odex_location": job.field("odex_location"),
        "liner": job.field("odex_liner"),
        # CMA CGM invoices the master bill.
        "mbl_no": mbl,
        "delivery_type": ODEX_DELIVERY.get(delivery, delivery),
        "job_no": job.shipment.job_no,
        # The request's required document: the MBL, as the Seaway BL.
        "mbl_file": job.document("mbl", f"MBL-{mbl}.pdf") if mbl else "",
    }


def invoice_pdf_path(rt: Runtime, shipment: Shipment) -> str:
    """Where the DO invoice from ODeX is kept, for the mail to Finance."""
    mbl = shipment.data.get("mbl_no") or ""
    return str(assemble.shipment_dir(rt.settings, shipment.id) / "portal" / f"DO-invoice-{mbl}.pdf")


def _invoice_fetch(job: _Job, hbl: str | None) -> dict[str, str]:
    """The team's ODeX guide, steps 6-10: the invoice goes to Finance, with the security deposit."""
    return {
        "odex_location": job.field("odex_location"),
        "mbl_no": job.field("mbl_no"),
        "invoice_save_path": invoice_pdf_path(job.rt, job.shipment),
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
    """The team's guide "Security deposit number 1": one request per house bill, refunded
    to the CHA as a third party. Mode Of Payment is Non-ODeX-RTGS every time (the SOP's
    own): Finance pays CMA CGM by RTGS."""
    if job.field("deposit_refund_to") == "Consignee":
        raise service.NotReady(
            "ODeX's deposit asks for the account the refund goes to, and the fixed details have only the CHA's. "
            "Raise a deposit refunded to the consignee on ODeX by hand."
        )
    return {
        "odex_location": job.field("odex_location"),
        "liner": job.field("odex_liner"),
        # ODeX takes a deposit per B/L: each house bill gets its own, with the MBL beside it.
        "hbl_no": hbl or "",
        "mbl_no": job.field("mbl_no"),
        "refund_party": job.field("refund_party"),
        # The refund goes to the CHA, not to the consignee who paid: the consignee's NOC.
        "noc_file": job.refund_noc(),
        "bank": job.field("payer_bank"),
        "amount": job.money("deposit_amount"),
        "utr": job.field("deposit_utr"),
        "payment_date": job.day_month_year("payment_date"),
        "ireceipt_no": job.field("security_receipt_no"),
        "beneficiary_name": job.field("refund_party"),
        "refund_bank": job.field("refund_bank"),
        "refund_branch": job.field("refund_branch"),
        "refund_account_type": job.field("refund_account_type"),
        "refund_account_no": job.field("refund_account_no"),
        "refund_ifsc": job.field("refund_ifsc"),
    }


def _deposit_status(job: _Job, hbl: str | None) -> dict[str, str]:
    return {
        "odex_location": job.field("odex_location"),
        "hbl_no": hbl or "",
        "slip_save_path": job.portal_file(f"{hbl}-security-deposit-slip.pdf"),
    }


def _do_request(job: _Job, hbl: str | None) -> dict[str, str]:
    """The team's guide "DO final steps corrected", steps 1-5. Cargo Type is General
    every time (the SOP's own)."""
    hbl_no = hbl or ""
    delivery = job.field("delivery_type")
    endorsed = job.field("endorsed")
    return {
        # ePayment is opened for the MBL's place of delivery, as for the invoice.
        "odex_location": job.field("odex_location"),
        "liner": job.field("odex_liner"),
        # ODeX wants the house bill here, not the master bill: "If IGM has been filed
        # for HBL, please submit a DO Request on ODeX for the respective HBL."
        "hbl_no": hbl_no,
        "destuff_type": ODEX_DESTUFF.get(delivery, delivery),
        "seaway_bl": ("Yes" if endorsed in SEAWAY_ENDORSEMENTS else "No") if endorsed else "",
        "runner_boy": job.optional("odex_runner_boy"),
        "runner_mobile": job.optional("odex_runner_mobile"),
        "remarks": job.field("odex_do_remarks"),
        "hss_customer_name": job.field("odex_hss_customer"),
        "factory_address": job.field("factory_address"),
        "factory_pin": job.field("factory_pin"),
        # One folder per MBL, uploaded with every house bill's request as "All
        # document in a single pdf", named as the team names it: ALL 157B.pdf for
        # HBL WOLSZSE26085157B.
        "folder_pdf_path": job.folder_pdf(f"ALL {hbl_no[-4:]}.pdf"),
    }


def _do_request_status(job: _Job, hbl: str | None) -> dict[str, str]:
    return {
        "odex_location": job.field("odex_location"),
        "hbl_no": hbl or "",
        "edo_save_path": job.portal_file(f"{hbl}-eDO.pdf"),
    }


@dataclass(frozen=True)
class PortalStep:
    key: str
    title: str  # what the step does, as ops would say it
    site: str
    sop: Path  # the SOP for the local version of the site (make portals)
    per_hbl: bool  # once per house bill, or once per MBL
    reads: tuple[str, ...]  # what the SOP reads back from the page
    build: Callable[[_Job, str | None], dict[str, str]]
    saves: tuple[tuple[str, str], ...] = ()  # what it read that goes on the job: (reading, field)


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
            INVOICE_FETCH, "Collecting the DO invoice from ODeX", ODEX,
            _local("odex/invoice_fetch.yaml"), False,
            ("odex_invoice_request_ref", "invoice_no", "invoice_amount", "invoice_date", "invoice_status",
             "downloaded_file_path"),
            _invoice_fetch, saves=(("invoice_no", "do_invoice_no"), ("invoice_amount", "do_invoice_amount")),
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
            _local("odex/deposit_new.yaml"), True, ("odex_deposit_ref", "odex_deposit_status"), _deposit,
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


def save_readings(rt: Runtime, shipment_id: str, step_key: str, reading: dict[str, Any]) -> dict[str, str]:
    """Puts on the job what a step read off a portal, for the fields the step names
    (PortalStep.saves), such as the DO invoice's number and amount from ODeX.
    Returns what it saved. These fields aren't in the DO folder, so a built folder
    stays current."""
    step = STEPS[step_key]
    found = {field: str(reading[key]).strip() for key, field in step.saves if str(reading.get(key) or "").strip()}
    if not found:
        return {}
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, shipment_id)
        problems = rt.folder.validate(found, "shipment")
        saved = {key: value for key, value in found.items() if key not in problems}
        if saved:
            shipment.data = {**shipment.data, **saved}
            read = ", ".join(f"{rt.folder.fields[key].label} {value}" for key, value in saved.items())
            service.log(session, shipment.id, "portal", f"{step.title}: read {read}.")
            session.commit()
    return saved


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
