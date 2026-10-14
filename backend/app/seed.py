"""Demo shipments for CMA CGM import DOs. The first one carries the sample
documents the customs team shared, when the Docs folder is present."""
from __future__ import annotations

import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import assemble
from .config import Settings
from .db import AutomationRun, Document, Event, IdempotencyKey, Profile, Shipment, Task
from .folder import Folder

PROFILE_ID = "default"
PROFILE_NAME = "Hexalog Global Supply Chains"

# Sample files in the Docs folder, by the folder slot they belong to.
SAMPLE_FILES = {
    "hbl": "House of Bill of Lading.pdf",
    "mbl": "Master Bill of Lading.pdf",
    "security_receipt": "Security Receipt.pdf",
    "security_odex_receipt": "Security Deposit Acknowledgment Slip.pdf",
    "do_receipt": "Delivery Order Receipt.pdf",
}


# A stand-in until the mailbox the Graph subscription watches is decided. The
# local portals never send email; they only show what CMA CGM would send.
DEMO_PROFILE = {"do_mailbox": "customs-ops@Hexalog.in"}


def _paid(n: str, days_ago: int, today: date) -> dict[str, str]:
    """The liner invoice and both payments, with made-up numbers that can't be
    mistaken for real ones."""
    return dict(
        do_invoice_no=f"INV-DEMO-{n}",
        do_invoice_amount="54720.68",
        payment_utr=f"DEMOUTR{n}INV",
        deposit_utr=f"DEMOUTR{n}DEP",
        payment_date=(today - timedelta(days=days_ago + 1)).isoformat(),
    )


def financial_year(today: date) -> str:
    """Indian financial year label, e.g. 26-27 for April 2026 to March 2027."""
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def _shipments(today: date) -> list[dict[str, Any]]:
    return [
        # Every document received: ready for review and build.
        dict(n="0431", owner="Ravi Sharma", status="deposit_approved", days_ago=3, files=list(SAMPLE_FILES), values=dict(
            mbl_no="GGZ3193024", hbl_nos="MSEC260803047251", containers="TCNU5191327",
            vessel="OOCL SINGAPORE", voyage="0FBI5W1MA", **_paid("0431", 3, today),
        )),
        # Two house bills, nothing received yet, voyage still unknown.
        dict(n="0434", owner="Sneha Nair", status="invoice_paid", days_ago=2, files=[], values=dict(
            mbl_no="GGZ3164994", hbl_nos="MSEC260900031273\nMSEC260900031466", containers="CMAU9987728",
            vessel="XIN MEI ZHOU", **_paid("0434", 2, today),
        )),
        # Someone attached job 0431's DO receipt here by mistake.
        dict(n="0436", owner="Arjun Verma", status="deposit_approved", days_ago=1, files=["do_receipt"], values=dict(
            mbl_no="GGZ3207715", hbl_nos="MSEC260900045112", containers="CMAU7412030",
            vessel="CMA CGM TAGE", voyage="0FE5LW1MA", **_paid("0436", 1, today),
        )),
        # IGM just filed.
        dict(n="0438", owner="Meera Iyer", status="igm_filed", days_ago=0, files=[], values=dict(
            mbl_no="GGZ3221408", vessel="APL PARIS",
        )),
    ]


def ensure_profile(session: Session, folder: Folder) -> None:
    """The fixed details exist from the first start, with the defaults from do_folder.yaml.
    A field added to do_folder.yaml later starts at its default, in the fixed details
    and in every job that has never had it."""
    today = date.today()
    profile = session.get(Profile, PROFILE_ID)
    if profile is None:
        session.add(Profile(id=PROFILE_ID, name=PROFILE_NAME, data=folder.defaults("profile", today)))
    elif new := _new_defaults(profile.data, folder.defaults("profile", today)):
        profile.data = {**profile.data, **new}
        # A new field is not an edit, so built folders stay current: the column is
        # set to itself, which also stops its onupdate from stamping the time.
        profile.updated_at = Profile.updated_at
    for shipment in session.scalars(select(Shipment)):
        if new := _new_defaults(shipment.data, folder.defaults("shipment", today)):
            shipment.data = {**shipment.data, **new}
    session.commit()


def _new_defaults(values: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Defaults for fields the values have never had. A field someone cleared stays clear."""
    return {key: value for key, value in defaults.items() if key not in values}


def seed_demo_data(session: Session, folder: Folder, settings: Settings) -> None:
    """Replaces everything in the database, and every stored file, with the demo data."""
    session.execute(delete(Event))
    session.execute(delete(Task))
    session.execute(delete(IdempotencyKey))
    session.execute(delete(AutomationRun))
    session.execute(delete(Document))
    session.execute(delete(Shipment))
    session.execute(delete(Profile))
    if settings.storage_dir.is_dir():
        shutil.rmtree(settings.storage_dir)

    today = date.today()
    session.add(Profile(id=PROFILE_ID, name=PROFILE_NAME, data={**folder.defaults("profile", today), **DEMO_PROFILE}))

    fy = financial_year(today)
    now = datetime.now(timezone.utc)
    for spec in _shipments(today):
        created = now - timedelta(days=spec["days_ago"])
        shipment = Shipment(
            id=uuid.uuid4().hex[:12],
            job_no=f"{settings.job_prefix}/{fy}/{spec['n']}",
            status=spec["status"],
            owner=spec["owner"],
            data={**folder.defaults("shipment", today), **spec["values"]},
            created_at=created,
            changed_at=created,
        )
        session.add(shipment)
        session.add(Event(shipment_id=shipment.id, at=created, action="created", detail=f"Job {shipment.job_no} opened."))

        slots = {slot.doc.key: slot for slot in folder.slots(shipment.data)}
        for key in spec["files"]:
            source = settings.demo_docs_dir / SAMPLE_FILES[key]
            if key not in slots or not source.is_file():
                continue
            document = assemble.store_upload(settings, shipment, slots[key], source.name, source.read_bytes())
            document.uploaded_at = created
            session.add(document)
            session.add(Event(
                shipment_id=shipment.id, at=created, action="attached",
                detail=f"{slots[key].doc.title}: {source.name}.",
            ))
    session.commit()
