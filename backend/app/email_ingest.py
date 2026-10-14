"""Database jobs for the inbound-email workflow. Blocking; run them with steps.job."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select

from . import assemble, containers, pdfs, service
from .db import Shipment, utcnow
from .folder import split_list
from .runtime import Runtime


def find_shipment_job(rt: Runtime, key: str) -> str | None:
    """The job whose MBL, one of its house bills, or one of its containers is ``key``."""
    with rt.sessionmaker() as session:
        for shipment in session.scalars(select(Shipment)):
            data = shipment.data
            if key == str(data.get("mbl_no", "")).strip().upper() or key in [
                *split_list(data.get("hbl_nos")),
                *containers.split(data.get("containers")),
            ]:
                return shipment.id
    return None


_CONTAINER_RE = re.compile(r"\b([A-Z]{3}[UJZ])[ -]?(\d{6})[ -]?(\d)\b")

# First match wins, so the longer names come first. From the sample file names in the shared Docs.
_ROUTES = (
    ("security deposit", "security_odex_receipt"),
    ("acknowledg", "security_odex_receipt"),
    ("security slip", "security_odex_receipt"),
    ("security receipt", "security_receipt"),
    ("delivery order receipt", "do_receipt"),
    ("do receipt", "do_receipt"),
    ("house", "hbl"),
    ("hbl", "hbl"),
    ("master", "mbl"),
    ("mbl", "mbl"),
)


def doc_key_for(filename: str) -> str | None:
    name = filename.lower()
    return next((key for needle, key in _ROUTES if needle in name), None)


_CONSIGNEE = re.compile(r"Consignee\s*\(Complete name and address\)[ \t]*\n?[ \t]*([^\n]+)")
_VESSEL = re.compile(r"Ocean vessel\.?\s*([A-Z0-9][A-Z0-9 .\-]*?)\s+V\.?\s*([A-Z0-9]{4,12})\b")


def extract_fields(key: str, text: str) -> dict[str, str]:
    """Shipment fields printed on a bill: the consignee, the vessel and the voyage.
    shortcut: read from CMA CGM's draft layout ("Ocean vessel. NAME V.VOYAGE"); change when it changes."""
    found: dict[str, str] = {}
    if consignee := _CONSIGNEE.search(text):
        found["consignee_mbl" if key == "mbl" else "consignee_hbl"] = consignee.group(1).strip().rstrip(".").strip()
    if vessel := _VESSEL.search(text):
        found["vessel"], found["voyage"] = vessel.group(1).strip(), vessel.group(2)
    return found


def _fillable(rt: Runtime, values: dict[str, Any], field: str) -> bool:
    """A field the bill may set: still empty, or still the folder's starting value."""
    current = values.get(field)
    return not current or current == getattr(rt.folder.fields.get(field), "default", None)


def create_shipment_job(rt: Runtime, mbl_no: str) -> str:
    """A new job for an MBL the mail names, as the New job button makes one."""
    from .api import STAGE_IDS, _next_job_no  # here, not at the top: api imports the workflows

    with rt.sessionmaker() as session:
        shipment = Shipment(
            id=uuid.uuid4().hex[:12],
            job_no=_next_job_no(session, rt.settings.job_prefix),
            status=STAGE_IDS[0],
            data={**rt.folder.defaults("shipment", date.today()), "mbl_no": mbl_no},
        )
        session.add(shipment)
        service.log(session, shipment.id, "created", f"Job {shipment.job_no} opened from an email.")
        session.commit()
        return shipment.id


def _attach(rt: Runtime, session, shipment: Shipment, slot, name: str, data: bytes) -> dict[str, Any]:  # noqa: ANN001
    """Puts a file in a slot, as api.upload_file does for an upload."""
    previous = service.attached(session, shipment, slot)
    if previous is not None and previous.sha256 == hashlib.sha256(data).hexdigest():
        return {"attached": False, "reason": "the same file is already attached", "slot_id": slot.id}
    document = assemble.store_upload(rt.settings, shipment, slot, name, data)
    if previous is not None:
        previous.current = False
    session.add(document)
    service.log(
        session, shipment.id, "replaced" if previous else "attached",
        f"{service.slot_name(slot)}: {document.original_name} (from email).",
    )
    shipment.changed_at = utcnow()
    return {"attached": True, "slot_id": slot.id, "document_id": document.id}


def route_files_job(rt: Runtime, shipment_id: str, files: list[dict[str, str]]) -> dict[str, Any]:
    """Files from one mail, each to its slot by file name. The bills also fill empty shipment
    fields: the HBL number from an HBL, the containers from the MBL. Typed values are never replaced."""
    attached: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    filled: set[str] = set()
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, shipment_id)
        for file in files:
            name = file["name"]
            data = Path(file["path"]).read_bytes()
            try:
                text = pdfs.extract_text(pdfs.to_pdf(data, name))
            except pdfs.UnreadableFile as exc:
                skipped.append({"name": name, "reason": str(exc)})
                continue
            key = doc_key_for(name)
            if key is None:  # an unhelpful file name: a house bill has an HBL number, the MBL has the job's
                if re.search(rt.settings.hbl_pattern, text):
                    key = "hbl"
                elif str(shipment.data.get("mbl_no", "")).strip().upper() in text.upper():
                    key = "mbl"
                else:
                    skipped.append({"name": name, "reason": "file not recognised"})
                    continue
            values = dict(shipment.data)
            hbls = split_list(values.get("hbl_nos"))
            slot_id = key
            if key in ("hbl", "security_odex_receipt"):
                found = re.search(rt.settings.hbl_pattern, text)
                number = found.group(0) if found else (hbls[0] if len(hbls) == 1 else None)
                if number is None:
                    skipped.append({"name": name, "reason": "no house bill number found in the file"})
                    continue
                if number not in hbls:
                    values["hbl_nos"] = "\n".join([*hbls, number])
                    filled.add("hbl_nos")
                slot_id = f"{key}:{number}"
            for field, value in extract_fields(key, text).items():
                if _fillable(rt, values, field) and values.get(field) != value:
                    values[field] = value
                    filled.add(field)
            if key in ("mbl", "hbl"):
                have = containers.split(values.get("containers"))
                new = [f"{a}{b}{c}" for a, b, c in _CONTAINER_RE.findall(text)]
                new = [c for c in dict.fromkeys(new) if c not in have and containers.problem(c) is None]
                if new:
                    values["containers"] = "\n".join([*have, *new])
                    filled.add("containers")
            shipment.data = values
            slot = service.find_slot(rt.folder, shipment, slot_id)
            if slot is None:
                skipped.append({"name": name, "reason": f"no slot {slot_id}"})
                continue
            attached.append({"name": name, **_attach(rt, session, shipment, slot, name, data)})
        session.commit()
    return {"attached": attached, "skipped": skipped, "filled": sorted(filled)}


def attach_file_job(rt: Runtime, shipment_id: str, slot_id: str, path: str, name: str) -> dict[str, Any]:
    """Attaches an emailed file to a slot, as api.upload_file does for an upload."""
    data = Path(path).read_bytes()
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, shipment_id)
        slot = service.find_slot(rt.folder, shipment, slot_id)
        if slot is None:
            raise service.NotFound(f"Shipment {shipment.job_no} has no slot {slot_id}.")
        if slot.doc.source == "fixed":
            raise service.NotReady(f"{slot.doc.title} is the same for every shipment and can't be replaced.")
        result = _attach(rt, session, shipment, slot, name, data)
        session.commit()
        return result


def record_job(rt: Runtime, shipment_id: str, stage: str, detail: str, fields: dict[str, str] | None = None) -> None:
    """Writes the mail to the job's history and fills the given fields where they are empty."""
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, shipment_id)
        empty = {k: v for k, v in (fields or {}).items() if not shipment.data.get(k)}
        if empty:
            shipment.data = {**shipment.data, **empty}
            shipment.changed_at = utcnow()
        service.log(session, shipment_id, f"email_{stage}", detail)
        session.commit()
