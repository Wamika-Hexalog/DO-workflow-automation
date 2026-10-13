"""Works out the state of every slot in a shipment's DO folder: which documents
are missing, and which need attention before the PDF can be built."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import containers
from .db import Document, Shipment
from .folder import Folder, Slot
from .pdfs import compact

# Fewer letters and digits than this and the file is treated as a scan without text.
MIN_TEXT = 40


@dataclass(frozen=True)
class Flag:
    kind: str  # missing, fields, duplicate, mismatch, unreadable, setup
    message: str
    can_check: bool = False  # an operator may clear it after checking by eye


@dataclass
class SlotState:
    slot: Slot
    source: str  # where this slot's pages come from now: fixed, upload or generate
    document: Document | None = None
    flags: list[Flag] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(f.kind in ("missing", "fields", "setup") for f in self.flags):
            return "missing"
        return "flagged" if self.flags else "ok"


@dataclass(frozen=True)
class Context:
    """What one shipment's checks need to know about the others."""

    jobs_by_mbl: dict[str, str]  # compact MBL number -> job number
    first_owner_of_file: dict[str, tuple[str, datetime]]  # sha256 -> (shipment id, uploaded at)
    job_by_shipment: dict[str, str]


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def build_context(shipments: list[Shipment], documents: list[Document]) -> Context:
    first: dict[str, tuple[str, datetime]] = {}
    for doc in sorted(documents, key=lambda d: _aware(d.uploaded_at)):
        first.setdefault(doc.sha256, (doc.shipment_id, _aware(doc.uploaded_at)))
    return Context(
        jobs_by_mbl={compact(s.data.get("mbl_no") or ""): s.job_no for s in shipments if s.data.get("mbl_no")},
        first_owner_of_file=first,
        job_by_shipment={s.id: s.job_no for s in shipments},
    )


def _identifiers(slot: Slot, keys: tuple[str, ...], values: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """For each match key: how to name it, and the values any one of which must appear."""
    result = []
    for key in keys:
        if key == "mbl_no" and values.get("mbl_no"):
            result.append(("MBL " + values["mbl_no"].upper(), [values["mbl_no"]]))
        elif key == "hbl" and slot.hbl:
            result.append(("HBL " + slot.hbl, [slot.hbl]))
        elif key == "containers" and (found := containers.split(values.get("containers"))):
            result.append(("container " + found[0], found))
    return result


def _file_flags(
    state: SlotState, keys: tuple[str, ...], shipment: Shipment, by_sha: dict[str, list[SlotState]], ctx: Context
) -> None:
    doc = state.document
    assert doc is not None
    others = [s for s in by_sha.get(doc.sha256, []) if s is not state]
    if others:
        names = ", ".join(s.slot.doc.title + (f" ({s.slot.hbl})" if s.slot.hbl else "") for s in others)
        state.flags.append(Flag("duplicate", f"The same file is also attached as {names}. Attach the right file to each."))

    owner = ctx.first_owner_of_file.get(doc.sha256)
    if owner and owner[0] != shipment.id and owner[1] < _aware(doc.uploaded_at) and not doc.checked_by_eye:
        job = ctx.job_by_shipment.get(owner[0], "another shipment")
        state.flags.append(Flag("duplicate", f"This exact file was already attached to job {job}.", can_check=True))

    if doc.checked_by_eye:
        return
    text = compact(doc.text)
    if len(text) < MIN_TEXT:
        state.flags.append(
            Flag(
                "unreadable",
                "This file has no readable text, so the app can't confirm it belongs to this shipment. "
                "Open it and check by eye.",
                can_check=True,
            )
        )
        return
    for name, candidates in _identifiers(state.slot, keys, shipment.data):
        if any(compact(c) in text for c in candidates):
            continue
        message = f"{name} does not appear in this file."
        mentioned = [
            job for mbl, job in ctx.jobs_by_mbl.items() if mbl and mbl in text and job != shipment.job_no
        ]
        if mentioned:
            message += f" It mentions the MBL of job {mentioned[0]}."
        state.flags.append(Flag("mismatch", message, can_check=True))


def folder_state(
    folder: Folder,
    shipment: Shipment,
    documents: list[Document],
    profile_values: dict[str, Any],
    private_dir: Path,
    ctx: Context,
) -> list[SlotState]:
    """One state per slot, in PDF order. `documents` are this shipment's current files."""
    attached = {(d.doc_key, d.hbl): d for d in documents}
    statuses = folder.field_statuses(profile_values, shipment.data)
    states: list[SlotState] = []

    for slot in folder.slots(shipment.data):
        doc = slot.doc
        upload = attached.get((doc.key, slot.hbl))
        state = SlotState(slot, "upload" if upload else doc.source, upload)
        states.append(state)

        # Uploads get stamped. A generated letter carries its own stamp, unless an
        # upload replaced it, which arrives signed.
        needs_stamp = doc.stamp is not None and (doc.source == "upload" or upload is None)
        if needs_stamp:
            stamp = folder.stamps[doc.stamp.stamp]
            if not (private_dir / "stamps" / stamp.file).is_file():
                state.flags.append(Flag("setup", f"The {stamp.name} stamp is missing. Run `make assets`."))

        if doc.source == "fixed":
            if not (private_dir / (doc.file or "")).is_file():
                state.flags.append(Flag("setup", f"{doc.file} is missing. Run `make assets`."))
            continue

        if upload is None:
            if doc.source == "upload":
                if doc.per_hbl and slot.hbl is None:
                    state.flags.append(Flag("missing", "Add the house B/L numbers in shipment details first."))
                else:
                    state.flags.append(Flag("missing", "Not received yet."))
                continue
            gaps = [
                folder.fields[key].label
                for key in doc.uses
                if statuses[key] in ("missing", "invalid") and not folder.fields[key].optional
            ]
            if gaps:
                state.flags.append(Flag("fields", "Fill in " + ", ".join(gaps) + "."))

    by_sha: dict[str, list[SlotState]] = {}
    for state in states:
        if state.document is not None:
            by_sha.setdefault(state.document.sha256, []).append(state)
    for state in states:
        if state.document is not None:
            # A generated document replaced by an upload must still be this shipment's.
            keys = state.slot.doc.match or ("mbl_no",)
            _file_flags(state, keys, shipment, by_sha, ctx)
    return states
