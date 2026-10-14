"""Storing uploads, rendering each slot of the folder as stamped pages, and
joining them into the PDF that goes to CMA CGM."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfWriter

from . import letters, pdfs
from .config import Settings
from .db import Document, Shipment
from .folder import Folder, Slot, print_value, split_list


def shipment_dir(settings: Settings, shipment_id: str) -> Path:
    return settings.storage_dir / shipment_id


def store_upload(
    settings: Settings, shipment: Shipment, slot: Slot, filename: str, data: bytes
) -> Document:
    """Keeps the file exactly as sent, plus a PDF copy, and reads its text for matching."""
    pdf = pdfs.to_pdf(data, filename)  # raises UnreadableFile
    doc_id = uuid.uuid4().hex[:16]
    folder = shipment_dir(settings, shipment.id)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()[:10] or ".bin"
    (folder / f"{doc_id}-original{suffix}").write_bytes(data)
    (folder / f"{doc_id}.pdf").write_bytes(pdf)
    return Document(
        id=doc_id,
        shipment_id=shipment.id,
        doc_key=slot.doc.key,
        hbl=slot.hbl,
        current=True,
        original_name=Path(filename).name[:255] or "upload",
        original_file=f"{doc_id}-original{suffix}",
        pdf_file=f"{doc_id}.pdf",
        sha256=hashlib.sha256(data).hexdigest(),
        page_count=pdfs.page_count(pdf),
        text=pdfs.extract_text(pdf),
    )


def letter_input(
    folder: Folder, settings: Settings, slot: Slot, profile_values: dict[str, Any], shipment_values: dict[str, Any]
) -> letters.LetterInput:
    stamp_key = slot.doc.stamp.stamp if slot.doc.stamp else None
    return letter_values(folder, settings, stamp_key, profile_values, shipment_values)


def letter_values(
    folder: Folder,
    settings: Settings,
    stamp_key: str | None,
    profile_values: dict[str, Any],
    shipment_values: dict[str, Any],
) -> letters.LetterInput:
    """Every field, as stored and as printed, for a letter signed with ``stamp_key``."""
    raw = {key: folder.value(key, profile_values, shipment_values) for key in folder.fields}
    printed = {key: print_value(folder.fields[key], raw[key], folder.date_format) for key in folder.fields}
    stamp = settings.private_dir / "stamps" / folder.stamps[stamp_key].file if stamp_key else None
    return letters.LetterInput(printed, raw, split_list(shipment_values.get("hbl_nos")), stamp, settings.assets_dir)


def add_slot(
    writer: PdfWriter,
    folder: Folder,
    settings: Settings,
    slot: Slot,
    document: Document | None,
    profile_values: dict[str, Any],
    shipment_values: dict[str, Any],
) -> None:
    """Appends one slot's pages, stamped where do_folder.yaml says."""
    doc = slot.doc
    if document is not None:
        path = shipment_dir(settings, document.shipment_id) / document.pdf_file
        # Uploads are stamped; a letter uploaded in place of a generated one arrives signed.
        placement = doc.stamp if doc.source == "upload" else None
        stamp = settings.private_dir / "stamps" / folder.stamps[placement.stamp].file if placement else None
        pdfs.add_pages(writer, path, placement=placement, stamp=stamp)
    elif doc.source == "fixed":
        pdfs.add_pages(writer, settings.private_dir / (doc.file or ""), pages=doc.pages)
    elif doc.source == "generate":
        render = letters.RENDERERS[doc.key]
        pdfs.add_pages(writer, render(letter_input(folder, settings, slot, profile_values, shipment_values)))
    else:
        raise ValueError(f"{doc.title} has no file yet.")


def slot_pdf(
    folder: Folder,
    settings: Settings,
    slot: Slot,
    document: Document | None,
    profile_values: dict[str, Any],
    shipment_values: dict[str, Any],
) -> bytes:
    writer = PdfWriter()
    add_slot(writer, folder, settings, slot, document, profile_values, shipment_values)
    return pdfs.write(writer)


def build_folder(
    folder: Folder,
    settings: Settings,
    shipment: Shipment,
    documents: list[Document],
    profile_values: dict[str, Any],
) -> Path:
    """The whole folder as one PDF, in order. Earlier builds are kept."""
    attached = {(d.doc_key, d.hbl): d for d in documents}
    writer = PdfWriter()
    for slot in folder.slots(shipment.data):
        add_slot(
            writer, folder, settings, slot, attached.get((slot.doc.key, slot.hbl)), profile_values, shipment.data
        )
    writer.add_metadata({"/Title": f"DO folder {shipment.job_no} {shipment.data.get('mbl_no', '')}".strip()})
    builds = shipment_dir(settings, shipment.id) / "builds"
    builds.mkdir(parents=True, exist_ok=True)
    safe_job = re.sub(r"[^A-Za-z0-9]+", "-", shipment.job_no).strip("-")
    mbl = re.sub(r"[^A-Za-z0-9]+", "", shipment.data.get("mbl_no") or "")
    path = builds / f"{safe_job}-{mbl}-DO-folder-{datetime.now():%Y%m%d-%H%M%S}.pdf"
    path.write_bytes(pdfs.write(writer))
    return path
