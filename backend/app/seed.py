"""Demo shipments for CMA CGM import DOs. The first one carries the sample
documents the customs team shared, when the Docs folder is present."""
from __future__ import annotations

import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from . import assemble
from .config import Settings
from .db import Document, Event, Profile, Shipment
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


def financial_year(today: date) -> str:
    """Indian financial year label, e.g. 26-27 for April 2026 to March 2027."""
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def _shipments() -> list[dict[str, Any]]:
    return [
        # Every document received: ready for review and build.
        dict(n="0431", owner="Ravi Sharma", status="deposit_approved", days_ago=3, files=list(SAMPLE_FILES), values=dict(
            mbl_no="GGZ3193024", hbl_nos="MSEC260803047251", containers="TCNU5191327",
            vessel="OOCL SINGAPORE", voyage="0FBI5W1MA",
        )),
        # Two house bills, nothing received yet, voyage still unknown.
        dict(n="0434", owner="Sneha Nair", status="invoice_paid", days_ago=2, files=[], values=dict(
            mbl_no="GGZ3164994", hbl_nos="MSEC260900031273\nMSEC260900031466", containers="CMAU9987728",
            vessel="XIN MEI ZHOU",
        )),
        # Someone attached job 0431's DO receipt here by mistake.
        dict(n="0436", owner="Arjun Verma", status="deposit_approved", days_ago=1, files=["do_receipt"], values=dict(
            mbl_no="GGZ3207715", hbl_nos="MSEC260900045112", containers="CMAU7412030",
            vessel="CMA CGM TAGE", voyage="0FE5LW1MA",
        )),
        # IGM just filed.
        dict(n="0438", owner="Meera Iyer", status="igm_filed", days_ago=0, files=[], values=dict(
            mbl_no="GGZ3221408", vessel="APL PARIS",
        )),
    ]


def ensure_profile(session: Session, folder: Folder) -> None:
    """The fixed details exist from the first start, with the defaults from do_folder.yaml."""
    if session.get(Profile, PROFILE_ID) is None:
        session.add(Profile(id=PROFILE_ID, name=PROFILE_NAME, data=folder.defaults("profile", date.today())))
        session.commit()


def seed_demo_data(session: Session, folder: Folder, settings: Settings) -> None:
    """Replaces everything in the database, and every stored file, with the demo data."""
    session.execute(delete(Event))
    session.execute(delete(Document))
    session.execute(delete(Shipment))
    session.execute(delete(Profile))
    if settings.storage_dir.is_dir():
        shutil.rmtree(settings.storage_dir)

    today = date.today()
    session.add(Profile(id=PROFILE_ID, name=PROFILE_NAME, data=folder.defaults("profile", today)))

    fy = financial_year(today)
    now = datetime.now(timezone.utc)
    for spec in _shipments():
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
