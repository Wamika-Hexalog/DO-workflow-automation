"""HTTP API for the board, the shipment forms, the DO folder and its PDF."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import assemble, checks, containers, letters, pdfs
from .config import Settings
from .db import Document, Event, Profile, Shipment, utcnow
from .folder import FOLDER_STAGE, STAGE_IDS, Folder, Slot
from .schemas import (
    BuildIn,
    EventOut,
    FileOut,
    FlagOut,
    FolderOut,
    MoveIn,
    ProfileOut,
    ShipmentIn,
    ShipmentOut,
    SlotOut,
    ValuesIn,
)
from .seed import PROFILE_ID, financial_year, seed_demo_data

MAX_UPLOAD = 25 * 1024 * 1024

router = APIRouter(prefix="/api")


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as session:
        yield session


def get_folder(request: Request) -> Folder:
    return request.app.state.folder


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SessionDep = Annotated[Session, Depends(get_session)]
FolderDep = Annotated[Folder, Depends(get_folder)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite drops time zones; stored times are UTC."""
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _profile(session: Session) -> Profile:
    profile = session.get(Profile, PROFILE_ID)
    if profile is None:
        raise HTTPException(503, "There are no fixed details yet. Reset the demo data or restart the API.")
    return profile


def _shipment(session: Session, shipment_id: str) -> Shipment:
    shipment = session.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Shipment not found.")
    return shipment


def _check(folder: Folder, values: dict[str, Any], scope: str) -> None:
    errors = folder.validate(values, scope)
    if errors:
        raise HTTPException(422, {"message": "Some values could not be saved.", "fields": errors})


def _current_docs(session: Session, shipment_id: str | None = None) -> list[Document]:
    query = select(Document).where(Document.current.is_(True))
    if shipment_id:
        query = query.where(Document.shipment_id == shipment_id)
    return list(session.scalars(query))


def _context(session: Session) -> checks.Context:
    return checks.build_context(list(session.scalars(select(Shipment))), _current_docs(session))


def _slot(folder: Folder, shipment: Shipment, slot_id: str) -> Slot:
    slot = next((s for s in folder.slots(shipment.data) if s.id == slot_id), None)
    if slot is None:
        raise HTTPException(404, "That document is not part of this shipment's folder.")
    return slot


def _attached(session: Session, shipment: Shipment, slot: Slot) -> Document | None:
    return session.scalar(
        select(Document).where(
            Document.shipment_id == shipment.id,
            Document.doc_key == slot.doc.key,
            Document.hbl.is_(None) if slot.hbl is None else Document.hbl == slot.hbl,
            Document.current.is_(True),
        )
    )


def _log(session: Session, shipment: Shipment, action: str, detail: str) -> None:
    session.add(Event(shipment_id=shipment.id, action=action, detail=detail))


def _slot_name(slot: Slot) -> str:
    return slot.doc.title + (f" for HBL {slot.hbl}" if slot.hbl else "")


def _stale(shipment: Shipment, profile: Profile) -> bool:
    built = _aware(shipment.folder_built_at)
    if built is None:
        return True
    return max(_aware(shipment.changed_at), _aware(profile.updated_at)) > built


def profile_out(profile: Profile) -> ProfileOut:
    return ProfileOut(id=profile.id, name=profile.name, values=profile.data)


def shipment_out(
    shipment: Shipment,
    profile: Profile,
    folder: Folder,
    settings: Settings,
    documents: list[Document],
    ctx: checks.Context,
) -> ShipmentOut:
    states = checks.folder_state(folder, shipment, documents, profile.data, settings.private_dir, ctx)
    base = f"/api/shipments/{shipment.id}"
    slots = []
    for state in states:
        doc = state.document
        slots.append(
            SlotOut(
                id=state.slot.id,
                doc_key=state.slot.doc.key,
                hbl=state.slot.hbl,
                title=state.slot.doc.title,
                source=state.source,
                defined_source=state.slot.doc.source,
                stamp=state.slot.doc.stamp.stamp if state.slot.doc.stamp else None,
                status=state.status,
                flags=[FlagOut(kind=f.kind, message=f.message, can_check=f.can_check) for f in state.flags],
                file=FileOut(
                    id=doc.id,
                    name=doc.original_name,
                    pages=doc.page_count,
                    uploaded_at=_aware(doc.uploaded_at),
                    checked_by_eye=doc.checked_by_eye,
                    original_url=f"{base}/slots/{state.slot.id}/original",
                )
                if doc
                else None,
                preview_url=f"{base}/slots/{state.slot.id}/preview" if state.status != "missing" else None,
            )
        )
    issues = {
        key: found
        for key, field in folder.fields.items()
        if field.type == "containers" and (found := containers.problems(shipment.data.get(key)))
    }
    folder_out = None
    if shipment.folder_file:
        folder_out = FolderOut(
            url=f"{base}/folder.pdf",
            filename=shipment.folder_file,
            built_at=_aware(shipment.folder_built_at),
            stale=_stale(shipment, profile),
        )
    return ShipmentOut(
        id=shipment.id,
        job_no=shipment.job_no,
        status=shipment.status,
        owner=shipment.owner,
        values=shipment.data,
        field_status=folder.field_statuses(profile.data, shipment.data),
        issues=issues,
        slots=slots,
        docs_ok=sum(s.status == "ok" for s in states),
        docs_total=len(states),
        ready=all(s.status == "ok" for s in states),
        folder=folder_out,
        requested_on=shipment.requested_on,
        edo_received_on=shipment.edo_received_on,
        updated_at=_aware(shipment.updated_at),
    )


def _one(session: Session, shipment: Shipment, folder: Folder, settings: Settings) -> ShipmentOut:
    return shipment_out(
        shipment, _profile(session), folder, settings, _current_docs(session, shipment.id), _context(session)
    )


def _next_job_no(session: Session, prefix: str) -> str:
    base = f"{prefix}/{financial_year(date.today())}/"
    taken = session.scalars(select(Shipment.job_no).where(Shipment.job_no.startswith(base, autoescape=True)))
    numbers = [int(tail) for job_no in taken if (tail := job_no.removeprefix(base)).isdigit()]
    return f"{base}{max(numbers, default=0) + 1:04d}"


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/folder")
def read_folder(folder: FolderDep) -> dict[str, Any]:
    """Documents, fields and stages. The UI builds its forms from this."""
    return folder.to_api()


@router.get("/profile", response_model=ProfileOut)
def read_profile(session: SessionDep) -> ProfileOut:
    return profile_out(_profile(session))


@router.patch("/profile", response_model=ProfileOut)
def update_profile(body: ValuesIn, session: SessionDep, folder: FolderDep) -> ProfileOut:
    _check(folder, body.values, "profile")
    profile = _profile(session)
    profile.data = {**profile.data, **body.values}
    session.commit()
    return profile_out(profile)


@router.get("/shipments", response_model=list[ShipmentOut])
def list_shipments(session: SessionDep, folder: FolderDep, settings: SettingsDep) -> list[ShipmentOut]:
    profile = _profile(session)
    shipments = list(session.scalars(select(Shipment).order_by(Shipment.created_at.desc())))
    documents = _current_docs(session)
    ctx = checks.build_context(shipments, documents)
    by_shipment: dict[str, list[Document]] = {}
    for doc in documents:
        by_shipment.setdefault(doc.shipment_id, []).append(doc)
    return [shipment_out(s, profile, folder, settings, by_shipment.get(s.id, []), ctx) for s in shipments]


@router.post("/shipments", response_model=ShipmentOut, status_code=201)
def create_shipment(body: ShipmentIn, session: SessionDep, folder: FolderDep, settings: SettingsDep) -> ShipmentOut:
    _check(folder, body.values, "shipment")
    shipment = Shipment(
        id=uuid.uuid4().hex[:12],
        job_no=_next_job_no(session, settings.job_prefix),
        status=STAGE_IDS[0],
        owner=body.owner,
        data={**folder.defaults("shipment", date.today()), **body.values},
    )
    session.add(shipment)
    _log(session, shipment, "created", f"Job {shipment.job_no} opened.")
    session.commit()
    return _one(session, shipment, folder, settings)


@router.get("/shipments/{shipment_id}", response_model=ShipmentOut)
def read_shipment(shipment_id: str, session: SessionDep, folder: FolderDep, settings: SettingsDep) -> ShipmentOut:
    return _one(session, _shipment(session, shipment_id), folder, settings)


@router.patch("/shipments/{shipment_id}/values", response_model=ShipmentOut)
def update_values(
    shipment_id: str, body: ValuesIn, session: SessionDep, folder: FolderDep, settings: SettingsDep
) -> ShipmentOut:
    """Saves only the fields sent. The UI calls this as the user types."""
    _check(folder, body.values, "shipment")
    shipment = _shipment(session, shipment_id)
    shipment.data = {**shipment.data, **body.values}
    shipment.changed_at = utcnow()
    session.commit()
    return _one(session, shipment, folder, settings)


@router.post("/shipments/{shipment_id}/move", response_model=ShipmentOut)
def move_shipment(
    shipment_id: str, body: MoveIn, session: SessionDep, folder: FolderDep, settings: SettingsDep
) -> ShipmentOut:
    shipment = _shipment(session, shipment_id)
    if body.to == shipment.status:
        return _one(session, shipment, folder, settings)
    if STAGE_IDS.index(body.to) >= STAGE_IDS.index(FOLDER_STAGE):
        if not shipment.folder_file:
            raise HTTPException(409, "Build the DO folder before moving this shipment to DO folder ready or later.")
        if _stale(shipment, _profile(session)):
            raise HTTPException(409, "The documents changed after the DO folder was built. Build it again first.")
    today = date.today()
    if body.to == "do_requested" and shipment.requested_on is None:
        shipment.requested_on = today
    if body.to == "edo_received" and shipment.edo_received_on is None:
        shipment.edo_received_on = today
    _log(session, shipment, "moved", f"Stage changed from {shipment.status} to {body.to}.")
    shipment.status = body.to
    session.commit()
    return _one(session, shipment, folder, settings)


@router.post("/shipments/{shipment_id}/slots/{slot_id}/file", response_model=ShipmentOut)
def upload_file(
    shipment_id: str,
    slot_id: str,
    session: SessionDep,
    folder: FolderDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
) -> ShipmentOut:
    """Attaches a file to a slot. A file already there is kept in the history, not deleted."""
    shipment = _shipment(session, shipment_id)
    slot = _slot(folder, shipment, slot_id)
    if slot.doc.source == "fixed":
        raise HTTPException(409, f"{slot.doc.title} is the same for every shipment and can't be replaced here.")
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "That file is larger than 25 MB.")
    if not data:
        raise HTTPException(422, "That file is empty.")
    try:
        document = assemble.store_upload(settings, shipment, slot, file.filename or "upload.pdf", data)
    except pdfs.UnreadableFile as exc:
        raise HTTPException(422, str(exc)) from exc
    previous = _attached(session, shipment, slot)
    if previous is not None:
        previous.current = False
    session.add(document)
    action = "replaced" if previous else "attached"
    _log(session, shipment, action, f"{_slot_name(slot)}: {document.original_name}.")
    shipment.changed_at = utcnow()
    session.commit()
    return _one(session, shipment, folder, settings)


@router.delete("/shipments/{shipment_id}/slots/{slot_id}/file", response_model=ShipmentOut)
def remove_file(
    shipment_id: str, slot_id: str, session: SessionDep, folder: FolderDep, settings: SettingsDep
) -> ShipmentOut:
    """Takes the file out of the folder. The file itself is kept for the audit trail."""
    shipment = _shipment(session, shipment_id)
    slot = _slot(folder, shipment, slot_id)
    document = _attached(session, shipment, slot)
    if document is None:
        raise HTTPException(404, "There is no file attached there.")
    document.current = False
    _log(session, shipment, "removed", f"{_slot_name(slot)}: {document.original_name}.")
    shipment.changed_at = utcnow()
    session.commit()
    return _one(session, shipment, folder, settings)


@router.post("/shipments/{shipment_id}/slots/{slot_id}/check", response_model=ShipmentOut)
def check_by_eye(
    shipment_id: str, slot_id: str, session: SessionDep, folder: FolderDep, settings: SettingsDep
) -> ShipmentOut:
    """The operator opened the file and confirms it belongs to this shipment."""
    shipment = _shipment(session, shipment_id)
    slot = _slot(folder, shipment, slot_id)
    document = _attached(session, shipment, slot)
    if document is None:
        raise HTTPException(404, "There is no file attached there.")
    document.checked_by_eye = True
    _log(session, shipment, "checked", f"{_slot_name(slot)}: confirmed by eye.")
    session.commit()
    return _one(session, shipment, folder, settings)


@router.get("/shipments/{shipment_id}/slots/{slot_id}/preview")
def preview_slot(
    shipment_id: str, slot_id: str, session: SessionDep, folder: FolderDep, settings: SettingsDep
) -> Response:
    """The pages exactly as they will appear in the folder, stamps included."""
    shipment = _shipment(session, shipment_id)
    slot = _slot(folder, shipment, slot_id)
    try:
        pdf = assemble.slot_pdf(
            folder, settings, slot, _attached(session, shipment, slot), _profile(session).data, shipment.data
        )
    except (letters.MissingAsset, pdfs.UnreadableFile, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(
        pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{slot.doc.key}.pdf"'}
    )


@router.get("/shipments/{shipment_id}/slots/{slot_id}/original")
def original_file(shipment_id: str, slot_id: str, session: SessionDep, folder: FolderDep, settings: SettingsDep):
    shipment = _shipment(session, shipment_id)
    document = _attached(session, shipment, _slot(folder, shipment, slot_id))
    if document is None:
        raise HTTPException(404, "There is no file attached there.")
    path = assemble.shipment_dir(settings, shipment.id) / document.original_file
    if not path.is_file():
        raise HTTPException(404, "The stored file is missing.")
    return FileResponse(path, filename=document.original_name)


@router.post("/shipments/{shipment_id}/build", response_model=ShipmentOut)
def build_folder(
    shipment_id: str, body: BuildIn, session: SessionDep, folder: FolderDep, settings: SettingsDep
) -> ShipmentOut:
    """Joins the folder into one PDF and moves the shipment to DO folder ready."""
    if not body.confirmed:
        raise HTTPException(422, "Confirm that you have reviewed every document before building the folder.")
    shipment = _shipment(session, shipment_id)
    profile = _profile(session)
    documents = _current_docs(session, shipment.id)
    states = checks.folder_state(folder, shipment, documents, profile.data, settings.private_dir, _context(session))
    open_slots = [s for s in states if s.status != "ok"]
    if open_slots:
        names = ", ".join(_slot_name(s.slot) for s in open_slots[:4])
        more = f" and {len(open_slots) - 4} more" if len(open_slots) > 4 else ""
        raise HTTPException(409, f"{len(open_slots)} documents need attention first: {names}{more}.")
    try:
        path = assemble.build_folder(folder, settings, shipment, documents, profile.data)
    except (letters.MissingAsset, pdfs.UnreadableFile, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    shipment.folder_file = path.name
    shipment.folder_built_at = utcnow()
    _log(session, shipment, "built", f"DO folder built after review: {path.name}.")
    if STAGE_IDS.index(shipment.status) < STAGE_IDS.index(FOLDER_STAGE):
        _log(session, shipment, "moved", f"Stage changed from {shipment.status} to {FOLDER_STAGE}.")
        shipment.status = FOLDER_STAGE
    session.commit()
    return _one(session, shipment, folder, settings)


@router.get("/shipments/{shipment_id}/folder.pdf")
def download_folder(shipment_id: str, session: SessionDep, settings: SettingsDep) -> FileResponse:
    shipment = _shipment(session, shipment_id)
    if not shipment.folder_file:
        raise HTTPException(404, "The DO folder has not been built yet.")
    path = assemble.shipment_dir(settings, shipment.id) / "builds" / shipment.folder_file
    if not path.is_file():
        raise HTTPException(404, "The built PDF is missing. Build the folder again.")
    return FileResponse(path, media_type="application/pdf", filename=shipment.folder_file)


@router.get("/shipments/{shipment_id}/events", response_model=list[EventOut])
def list_events(shipment_id: str, session: SessionDep) -> list[EventOut]:
    shipment = _shipment(session, shipment_id)
    rows = session.scalars(
        select(Event).where(Event.shipment_id == shipment.id).order_by(Event.at.desc(), Event.id.desc())
    )
    return [EventOut(at=_aware(e.at), action=e.action, detail=e.detail) for e in rows]


@router.post("/demo/reset")
def reset_demo_data(session: SessionDep, folder: FolderDep, settings: SettingsDep) -> dict[str, bool]:
    seed_demo_data(session, folder, settings)
    return {"ok": True}
