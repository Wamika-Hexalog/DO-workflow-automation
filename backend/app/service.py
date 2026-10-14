"""Operations on shipments shared by the API and the Inngest workflows.

The API calls the plain helpers inside a request's session. The workflows call
the ``*_job`` functions, which open their own session; they run in a worker
thread so a slow build never blocks the API.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import assemble, checks, tasks
from .db import Document, Event, Profile, Shipment, Task, utcnow
from .folder import FOLDER_STAGE, STAGE_IDS, Folder, Slot, split_list
from .runtime import Runtime
from .seed import PROFILE_ID

DO_REQUESTED = "do_requested"


class NotFound(LookupError):
    pass


class NotReady(Exception):
    """The folder can't be built, or the DO request raised, yet. The message says why."""


def aware(moment: datetime | None) -> datetime | None:
    """SQLite drops time zones; stored times are UTC."""
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def load_profile(session: Session) -> Profile:
    profile = session.get(Profile, PROFILE_ID)
    if profile is None:
        raise NotFound("There are no fixed details yet. Reset the demo data or restart the API.")
    return profile


def load_shipment(session: Session, shipment_id: str) -> Shipment:
    shipment = session.get(Shipment, shipment_id)
    if shipment is None:
        raise NotFound("Shipment not found.")
    return shipment


def current_docs(session: Session, shipment_id: str | None = None) -> list[Document]:
    query = select(Document).where(Document.current.is_(True))
    if shipment_id:
        query = query.where(Document.shipment_id == shipment_id)
    return list(session.scalars(query))


def check_context(session: Session) -> checks.Context:
    return checks.build_context(list(session.scalars(select(Shipment))), current_docs(session))


def find_slot(folder: Folder, shipment: Shipment, slot_id: str) -> Slot | None:
    return next((s for s in folder.slots(shipment.data) if s.id == slot_id), None)


def attached(session: Session, shipment: Shipment, slot: Slot) -> Document | None:
    return session.scalar(
        select(Document).where(
            Document.shipment_id == shipment.id,
            Document.doc_key == slot.doc.key,
            Document.hbl.is_(None) if slot.hbl is None else Document.hbl == slot.hbl,
            Document.current.is_(True),
        )
    )


def log(session: Session, shipment_id: str, action: str, detail: str) -> None:
    session.add(Event(shipment_id=shipment_id, action=action, detail=detail))


def slot_name(slot: Slot) -> str:
    return slot.doc.title + (f" for HBL {slot.hbl}" if slot.hbl else "")


def stale(shipment: Shipment, profile: Profile) -> bool:
    built = aware(shipment.folder_built_at)
    if built is None:
        return True
    return max(aware(shipment.changed_at), aware(profile.updated_at)) > built


def slot_states(rt: Runtime, session: Session, shipment: Shipment, profile: Profile) -> list[checks.SlotState]:
    return checks.folder_state(
        rt.folder,
        shipment,
        current_docs(session, shipment.id),
        profile.data,
        rt.settings.private_dir,
        check_context(session),
    )


def is_building(review: Task | None, shipment: Shipment) -> bool:
    """Ops confirmed the folder and the build hasn't finished yet."""
    if review is None or review.status != tasks.COMPLETED or review.completed_at is None:
        return False
    built = aware(shipment.folder_built_at)
    return built is None or built < aware(review.completed_at)


def move_to(session: Session, shipment: Shipment, stage: str) -> None:
    log(session, shipment.id, "moved", f"Stage changed from {shipment.status} to {stage}.")
    shipment.status = stage


def readiness(states: list[checks.SlotState]) -> tuple[bool, str]:
    missing = sum(s.status == "missing" for s in states)
    flagged = sum(s.status == "flagged" for s in states)
    if not missing and not flagged:
        return True, f"All {len(states)} documents are in place. Review them, then build the folder."
    parts = [f"{missing} missing" if missing else "", f"{flagged} to check" if flagged else ""]
    return False, f"{len(states) - missing - flagged} of {len(states)} documents ready: " + ", ".join(p for p in parts if p) + "."


def open_slots_message(states: list[checks.SlotState]) -> str | None:
    open_slots = [s for s in states if s.status != "ok"]
    if not open_slots:
        return None
    names = ", ".join(slot_name(s.slot) for s in open_slots[:4])
    more = f" and {len(open_slots) - 4} more" if len(open_slots) > 4 else ""
    return f"{len(open_slots)} documents need attention first: {names}{more}."


# Jobs the workflows run, each in its own session -------------------------


def refresh_folder_review_job(rt: Runtime, shipment_id: str) -> dict[str, Any]:
    """Keeps the "Review and build" task in line with the documents and fields:
    in front of ops when everything is in place, waiting otherwise, and taken
    back if a built folder has gone out of date."""
    with rt.sessionmaker() as session:
        shipment = load_shipment(session, shipment_id)
        profile = load_profile(session)
        ready, note = readiness(slot_states(rt, session, shipment, profile))
        review = tasks.ensure(session, shipment.id, tasks.FOLDER_REVIEW)
        before = review.status
        if review.status == tasks.COMPLETED:
            if not is_building(review, shipment) and stale(shipment, profile):
                tasks.reopen(session, shipment.id, tasks.FOLDER_REVIEW, ready=ready, note=note)
                log(session, shipment.id, "task", "The DO folder changed after it was built, so it needs review again.")
        elif ready:
            tasks.signal_ready(session, shipment.id, tasks.FOLDER_REVIEW, note)
        else:
            tasks.set_waiting(session, shipment.id, tasks.FOLDER_REVIEW, note)
        became_ready = before != tasks.AWAITING_APPROVAL and review.status == tasks.AWAITING_APPROVAL
        if became_ready:
            log(session, shipment.id, "task", note)
        session.commit()
        return {"ready": ready, "status": review.status, "became_ready": became_ready}


def build_folder_job(rt: Runtime, shipment_id: str) -> dict[str, Any]:
    """Builds the PDF ops confirmed and moves the job to DO folder ready."""
    with rt.sessionmaker() as session:
        shipment = load_shipment(session, shipment_id)
        profile = load_profile(session)
        if message := open_slots_message(slot_states(rt, session, shipment, profile)):
            raise NotReady(message)
        path = assemble.build_folder(rt.folder, rt.settings, shipment, current_docs(session, shipment.id), profile.data)
        shipment.folder_file = path.name
        shipment.folder_built_at = utcnow()
        log(session, shipment.id, "built", f"DO folder built after review: {path.name}.")
        moved = STAGE_IDS.index(shipment.status) < STAGE_IDS.index(FOLDER_STAGE)
        if moved:
            move_to(session, shipment, FOLDER_STAGE)
        session.commit()
        return {"filename": path.name, "moved": moved}


def odex_note(rt: Runtime, hbls: list[str]) -> str:
    count = f"{len(hbls)} house bill{'s' if len(hbls) != 1 else ''} ({', '.join(hbls)})"
    if rt.settings.odex_automated:
        return (
            f"Approve to raise the DO request on ODeX for {count}. The browser agent fills in each HBL "
            "number and uploads the folder PDF."
        )
    return (
        f"ODeX isn't automated yet. Raise the DO request for {count} on ODeX (e-Payment → DO Request → "
        "New DO Request, using the HBL number), upload the folder PDF, then mark it done here."
    )


def open_do_request_job(rt: Runtime, shipment_id: str) -> dict[str, Any]:
    """Puts the ODeX DO request in front of ops once the folder is built."""
    with rt.sessionmaker() as session:
        shipment = load_shipment(session, shipment_id)
        hbls = split_list(shipment.data.get("hbl_nos"))
        if STAGE_IDS.index(shipment.status) >= STAGE_IDS.index(DO_REQUESTED):
            log(
                session,
                shipment.id,
                "task",
                "The folder was rebuilt after the DO request was raised. Upload the new PDF on ODeX by hand.",
            )
            session.commit()
            return {"opened": False}
        opened = tasks.signal_ready(session, shipment.id, tasks.ODEX_DO_REQUEST, odex_note(rt, hbls))
        session.commit()
        return {"opened": opened, "automated": rt.settings.odex_automated, "hbls": hbls}


def do_request_plan_job(rt: Runtime, shipment_id: str) -> dict[str, Any]:
    """Everything the DO request needs, checked up front."""
    with rt.sessionmaker() as session:
        shipment = load_shipment(session, shipment_id)
        profile = load_profile(session)
        if not shipment.folder_file:
            raise NotReady("Build the DO folder before raising the DO request.")
        if stale(shipment, profile):
            raise NotReady("The DO folder changed after it was built. Build it again before raising the DO request.")
        hbls = split_list(shipment.data.get("hbl_nos"))
        if not hbls:
            raise NotReady("Add the house B/L numbers before raising the DO request.")
        pdf = assemble.shipment_dir(rt.settings, shipment.id) / "builds" / shipment.folder_file
        if not pdf.is_file():
            raise NotReady("The built folder PDF is missing. Build the folder again.")
        # The SOP's params come from portal_params, in their own step.
        return {"automated": rt.settings.odex_automated, "sop": str(rt.settings.odex_sop_path), "hbls": hbls}


def record_do_request_job(rt: Runtime, shipment_id: str, results: dict[str, dict[str, Any]], by: str) -> dict[str, Any]:
    """Logs each HBL's request and moves the job to DO requested."""
    with rt.sessionmaker() as session:
        shipment = load_shipment(session, shipment_id)
        lines = []
        for hbl, result in results.items():
            if result.get("by_hand"):
                line = f"DO request raised on ODeX by hand for HBL {hbl}, confirmed by {by}."
            else:
                reference = result.get("reference")
                line = f"DO request raised on ODeX for HBL {hbl}" + (f", reference {reference}." if reference else ".")
            log(session, shipment.id, "do_request", line)
            lines.append(line)
        if STAGE_IDS.index(shipment.status) < STAGE_IDS.index(DO_REQUESTED):
            move_to(session, shipment, DO_REQUESTED)
        if shipment.requested_on is None:
            shipment.requested_on = date.today()
        tasks.record_result(session, shipment.id, tasks.ODEX_DO_REQUEST, " ".join(lines), {"hbls": results})
        session.commit()
        return {"hbls": list(results)}


def fail_task_job(rt: Runtime, shipment_id: str, task_key: str, message: str) -> None:
    """A workflow behind ``task_key`` failed: put the error in front of ops."""
    with rt.sessionmaker() as session:
        if session.get(Shipment, shipment_id) is None:
            return
        tasks.fail(session, shipment_id, task_key, message)
        log(session, shipment_id, "failed", f"{tasks.TITLES.get(task_key, task_key)}: {message}")
        session.commit()


def folder_pdf_path(rt: Runtime, shipment: Shipment) -> Path | None:
    if not shipment.folder_file:
        return None
    return assemble.shipment_dir(rt.settings, shipment.id) / "builds" / shipment.folder_file
