"""HTTP API for the board, the shipment forms, the DO folder, its PDF and the ops tasks.

Saving a change is the API's job; what follows from it is the workflows' job. Each
change is saved first, then announced to Inngest:

* uploads and edits send a notice the folder check picks up. It is best-effort:
  if the workflow engine is down the change still stands, and the next one
  catches the check up;
* building the folder and approving the DO request complete a task, and the
  event they send starts the work. If it can't be sent the task is put back and
  the caller hears why, so nothing waits on an event that never left.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Annotated, Any

import httpx
import inngest
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import assemble, checks, containers, letters, pdfs, service, tasks
from .db import AutomationRun, Document, Event, Profile, Shipment, Task, utcnow
from .folder import FOLDER_STAGE, STAGE_IDS, Folder, Slot, split_list
from .runtime import Runtime
from .schemas import (
    BuildIn,
    EventOut,
    FileOut,
    FlagOut,
    FolderOut,
    MoveIn,
    ProfileOut,
    RunOut,
    ShipmentIn,
    ShipmentOut,
    SlotOut,
    TaskCompleteIn,
    TaskOut,
    ValuesIn,
)
from .seed import financial_year, seed_demo_data
from .workflows.events import EventNames

logger = logging.getLogger(__name__)

MAX_UPLOAD = 25 * 1024 * 1024
ENGINE_DOWN = "The workflow engine isn't running, so nothing was started. Start it with make dev, then try again."

router = APIRouter(prefix="/api")


def get_runtime(request: Request) -> Runtime:
    return request.app.state.rt


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.rt.sessionmaker() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


class EngineUnavailable(Exception):
    pass


def _send(rt: Runtime, name: str, payloads: list[dict[str, Any]]) -> None:
    if payloads:
        try:
            rt.inngest.send_sync([inngest.Event(name=name, data=payload) for payload in payloads])
        except Exception as exc:
            raise EngineUnavailable(str(exc)) from exc


# One worker: notices leave in the order they were made, and a request never waits
# for one. With the engine down a send takes seconds to give up.
_notices = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inngest-notice")


def _notify(rt: Runtime, name: str, payloads: list[dict[str, Any]]) -> None:
    """Best-effort: the change is saved whether or not the engine hears about it."""

    def send() -> None:
        try:
            _send(rt, name, payloads)
        except EngineUnavailable as exc:
            logger.warning("Inngest did not get %s: %s", name, exc)

    _notices.submit(send)


def flush_notices() -> None:
    """Waits until every notice so far has been sent. For tests."""
    _notices.submit(lambda: None).result()


def _profile(session: Session) -> Profile:
    try:
        return service.load_profile(session)
    except service.NotFound as exc:
        raise HTTPException(503, str(exc)) from exc


def _shipment(session: Session, shipment_id: str) -> Shipment:
    try:
        return service.load_shipment(session, shipment_id)
    except service.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


def _slot(folder: Folder, shipment: Shipment, slot_id: str) -> Slot:
    slot = service.find_slot(folder, shipment, slot_id)
    if slot is None:
        raise HTTPException(404, "That document is not part of this shipment's folder.")
    return slot


def _check(folder: Folder, values: dict[str, Any], scope: str) -> None:
    errors = folder.validate(values, scope)
    if errors:
        raise HTTPException(422, {"message": "Some values could not be saved.", "fields": errors})


def profile_out(profile: Profile) -> ProfileOut:
    return ProfileOut(id=profile.id, name=profile.name, values=profile.data)


def task_out(task: Task) -> TaskOut:
    return TaskOut(
        key=task.task_key,
        title=tasks.TITLES.get(task.task_key, task.task_key),
        status=task.status,
        note=task.note or "",
        completed_at=service.aware(task.completed_at),
        completed_by=task.completed_by,
        result=task.result or {},
    )


def shipment_out(
    rt: Runtime,
    shipment: Shipment,
    profile: Profile,
    documents: list[Document],
    ctx: checks.Context,
    shipment_tasks: list[Task],
) -> ShipmentOut:
    folder = rt.folder
    states = checks.folder_state(folder, shipment, documents, profile.data, rt.settings.private_dir, ctx)
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
                    uploaded_at=service.aware(doc.uploaded_at),
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
            built_at=service.aware(shipment.folder_built_at),
            stale=service.stale(shipment, profile),
        )
    review = next((t for t in shipment_tasks if t.task_key == tasks.FOLDER_REVIEW), None)
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
        building=service.is_building(review, shipment),
        tasks=[task_out(t) for t in shipment_tasks],
        requested_on=shipment.requested_on,
        edo_received_on=shipment.edo_received_on,
        updated_at=service.aware(shipment.updated_at),
    )


def _one(rt: Runtime, session: Session, shipment: Shipment) -> ShipmentOut:
    return shipment_out(
        rt,
        shipment,
        _profile(session),
        service.current_docs(session, shipment.id),
        service.check_context(session),
        tasks.for_shipment(session, shipment.id),
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
def read_folder(rt: RuntimeDep) -> dict[str, Any]:
    """Documents, fields and stages. The UI builds its forms from this."""
    return {**rt.folder.to_api(), "odex": {"automated": rt.settings.odex_automated}}


@router.get("/profile", response_model=ProfileOut)
def read_profile(session: SessionDep) -> ProfileOut:
    return profile_out(_profile(session))


@router.patch("/profile", response_model=ProfileOut)
def update_profile(body: ValuesIn, session: SessionDep, rt: RuntimeDep) -> ProfileOut:
    _check(rt.folder, body.values, "profile")
    profile = _profile(session)
    profile.data = {**profile.data, **body.values}
    session.commit()
    # Every shipment's letters print the fixed details.
    _notify(rt, EventNames.SHIPMENT_UPDATED, [{"shipment_id": sid} for sid in session.scalars(select(Shipment.id))])
    return profile_out(profile)


@router.get("/shipments", response_model=list[ShipmentOut])
def list_shipments(session: SessionDep, rt: RuntimeDep) -> list[ShipmentOut]:
    profile = _profile(session)
    shipments = list(session.scalars(select(Shipment).order_by(Shipment.created_at.desc())))
    documents = service.current_docs(session)
    ctx = checks.build_context(shipments, documents)
    by_shipment: dict[str, list[Document]] = {}
    for doc in documents:
        by_shipment.setdefault(doc.shipment_id, []).append(doc)
    tasks_by_shipment: dict[str, list[Task]] = {}
    for task in session.scalars(select(Task).order_by(Task.id)):
        tasks_by_shipment.setdefault(task.shipment_id, []).append(task)
    return [
        shipment_out(rt, s, profile, by_shipment.get(s.id, []), ctx, tasks_by_shipment.get(s.id, []))
        for s in shipments
    ]


@router.post("/shipments", response_model=ShipmentOut, status_code=201)
def create_shipment(body: ShipmentIn, session: SessionDep, rt: RuntimeDep) -> ShipmentOut:
    _check(rt.folder, body.values, "shipment")
    shipment = Shipment(
        id=uuid.uuid4().hex[:12],
        job_no=_next_job_no(session, rt.settings.job_prefix),
        status=STAGE_IDS[0],
        owner=body.owner,
        data={**rt.folder.defaults("shipment", date.today()), **body.values},
    )
    session.add(shipment)
    service.log(session, shipment.id, "created", f"Job {shipment.job_no} opened.")
    session.commit()
    return _one(rt, session, shipment)


@router.get("/shipments/{shipment_id}", response_model=ShipmentOut)
def read_shipment(shipment_id: str, session: SessionDep, rt: RuntimeDep) -> ShipmentOut:
    return _one(rt, session, _shipment(session, shipment_id))


@router.patch("/shipments/{shipment_id}/values", response_model=ShipmentOut)
def update_values(shipment_id: str, body: ValuesIn, session: SessionDep, rt: RuntimeDep) -> ShipmentOut:
    """Saves only the fields sent. The UI calls this as the user types."""
    _check(rt.folder, body.values, "shipment")
    shipment = _shipment(session, shipment_id)
    shipment.data = {**shipment.data, **body.values}
    shipment.changed_at = utcnow()
    session.commit()
    _notify(rt, EventNames.SHIPMENT_UPDATED, [{"shipment_id": shipment.id}])
    return _one(rt, session, shipment)


@router.post("/shipments/{shipment_id}/move", response_model=ShipmentOut)
def move_shipment(shipment_id: str, body: MoveIn, session: SessionDep, rt: RuntimeDep) -> ShipmentOut:
    shipment = _shipment(session, shipment_id)
    if body.to == shipment.status:
        return _one(rt, session, shipment)
    if STAGE_IDS.index(body.to) >= STAGE_IDS.index(FOLDER_STAGE):
        if not shipment.folder_file:
            raise HTTPException(409, "Build the DO folder before moving this shipment to DO folder ready or later.")
        if service.stale(shipment, _profile(session)):
            raise HTTPException(409, "The documents changed after the DO folder was built. Build it again first.")
    today = date.today()
    if body.to == "do_requested" and shipment.requested_on is None:
        shipment.requested_on = today
    if body.to == "edo_received" and shipment.edo_received_on is None:
        shipment.edo_received_on = today
    service.move_to(session, shipment, body.to)
    session.commit()
    return _one(rt, session, shipment)


@router.post("/shipments/{shipment_id}/slots/{slot_id}/file", response_model=ShipmentOut)
def upload_file(
    shipment_id: str,
    slot_id: str,
    session: SessionDep,
    rt: RuntimeDep,
    file: Annotated[UploadFile, File()],
) -> ShipmentOut:
    """Attaches a file to a slot. A file already there is kept in the history, not deleted."""
    shipment = _shipment(session, shipment_id)
    slot = _slot(rt.folder, shipment, slot_id)
    if slot.doc.source == "fixed":
        raise HTTPException(409, f"{slot.doc.title} is the same for every shipment and can't be replaced here.")
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "That file is larger than 25 MB.")
    if not data:
        raise HTTPException(422, "That file is empty.")
    try:
        document = assemble.store_upload(rt.settings, shipment, slot, file.filename or "upload.pdf", data)
    except pdfs.UnreadableFile as exc:
        raise HTTPException(422, str(exc)) from exc
    previous = service.attached(session, shipment, slot)
    if previous is not None:
        previous.current = False
    session.add(document)
    action = "replaced" if previous else "attached"
    service.log(session, shipment.id, action, f"{service.slot_name(slot)}: {document.original_name}.")
    shipment.changed_at = utcnow()
    session.commit()
    _notify(
        rt,
        EventNames.DOCUMENT_RECEIVED,
        [{"shipment_id": shipment.id, "slot_id": slot.id, "document_id": document.id, "source": "upload"}],
    )
    return _one(rt, session, shipment)


@router.delete("/shipments/{shipment_id}/slots/{slot_id}/file", response_model=ShipmentOut)
def remove_file(shipment_id: str, slot_id: str, session: SessionDep, rt: RuntimeDep) -> ShipmentOut:
    """Takes the file out of the folder. The file itself is kept for the audit trail."""
    shipment = _shipment(session, shipment_id)
    slot = _slot(rt.folder, shipment, slot_id)
    document = service.attached(session, shipment, slot)
    if document is None:
        raise HTTPException(404, "There is no file attached there.")
    document.current = False
    service.log(session, shipment.id, "removed", f"{service.slot_name(slot)}: {document.original_name}.")
    shipment.changed_at = utcnow()
    session.commit()
    _notify(rt, EventNames.SHIPMENT_UPDATED, [{"shipment_id": shipment.id}])
    return _one(rt, session, shipment)


@router.post("/shipments/{shipment_id}/slots/{slot_id}/check", response_model=ShipmentOut)
def check_by_eye(shipment_id: str, slot_id: str, session: SessionDep, rt: RuntimeDep) -> ShipmentOut:
    """The operator opened the file and confirms it belongs to this shipment."""
    shipment = _shipment(session, shipment_id)
    slot = _slot(rt.folder, shipment, slot_id)
    document = service.attached(session, shipment, slot)
    if document is None:
        raise HTTPException(404, "There is no file attached there.")
    document.checked_by_eye = True
    service.log(session, shipment.id, "checked", f"{service.slot_name(slot)}: confirmed by eye.")
    session.commit()
    _notify(rt, EventNames.SHIPMENT_UPDATED, [{"shipment_id": shipment.id}])
    return _one(rt, session, shipment)


@router.get("/shipments/{shipment_id}/slots/{slot_id}/preview")
def preview_slot(shipment_id: str, slot_id: str, session: SessionDep, rt: RuntimeDep) -> Response:
    """The pages exactly as they will appear in the folder, stamps included."""
    shipment = _shipment(session, shipment_id)
    slot = _slot(rt.folder, shipment, slot_id)
    try:
        pdf = assemble.slot_pdf(
            rt.folder,
            rt.settings,
            slot,
            service.attached(session, shipment, slot),
            _profile(session).data,
            shipment.data,
        )
    except (letters.MissingAsset, pdfs.UnreadableFile, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(
        pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{slot.doc.key}.pdf"'}
    )


@router.get("/shipments/{shipment_id}/slots/{slot_id}/original")
def original_file(shipment_id: str, slot_id: str, session: SessionDep, rt: RuntimeDep) -> FileResponse:
    shipment = _shipment(session, shipment_id)
    document = service.attached(session, shipment, _slot(rt.folder, shipment, slot_id))
    if document is None:
        raise HTTPException(404, "There is no file attached there.")
    path = assemble.shipment_dir(rt.settings, shipment.id) / document.original_file
    if not path.is_file():
        raise HTTPException(404, "The stored file is missing.")
    return FileResponse(path, filename=document.original_name)


def _complete_and_start(
    rt: Runtime, session: Session, shipment: Shipment, task_key: str, by: str, allowed: tuple[str, ...]
) -> None:
    """Completes the task, then sends the event that starts its workflow. If the
    event can't be sent the task goes back to how it was."""
    try:
        before = tasks.complete(session, shipment.id, task_key, by=by, allowed=allowed)
    except tasks.TaskConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    try:
        _send(rt, EventNames.TASK_COMPLETED, [{"shipment_id": shipment.id, "task_key": task_key, "completed_by": by}])
    except EngineUnavailable as exc:
        tasks.restore(session, shipment.id, task_key, before)
        session.commit()
        raise HTTPException(503, ENGINE_DOWN) from exc


@router.post("/shipments/{shipment_id}/build", response_model=ShipmentOut, status_code=202)
def build_folder(shipment_id: str, body: BuildIn, session: SessionDep, rt: RuntimeDep) -> ShipmentOut:
    """Ops confirm they reviewed the folder; the build workflow makes the PDF and
    moves the shipment to DO folder ready. Poll the shipment until ``building`` clears."""
    if not body.confirmed:
        raise HTTPException(422, "Confirm that you have reviewed every document before building the folder.")
    shipment = _shipment(session, shipment_id)
    profile = _profile(session)
    if service.is_building(tasks.get(session, shipment.id, tasks.FOLDER_REVIEW), shipment):
        raise HTTPException(409, "The DO folder is already being built.")
    if message := service.open_slots_message(service.slot_states(rt, session, shipment, profile)):
        raise HTTPException(409, message)
    by = body.by or "ops"
    _complete_and_start(rt, session, shipment, tasks.FOLDER_REVIEW, by, allowed=tasks.STATUSES)
    service.log(session, shipment.id, "task", f"Folder review confirmed by {by}. Building the DO folder.")
    session.commit()
    return _one(rt, session, shipment)


@router.get("/shipments/{shipment_id}/tasks", response_model=list[TaskOut])
def list_tasks(shipment_id: str, session: SessionDep) -> list[TaskOut]:
    shipment = _shipment(session, shipment_id)
    return [task_out(t) for t in tasks.for_shipment(session, shipment.id)]


@router.post("/shipments/{shipment_id}/tasks/{task_key}/complete", response_model=ShipmentOut, status_code=202)
def complete_task(
    shipment_id: str, task_key: str, body: TaskCompleteIn, session: SessionDep, rt: RuntimeDep
) -> ShipmentOut:
    """Ops approve a step: the DO request on ODeX (or confirm they raised it by hand), or
    a payment step. A step the agent runs by itself can only be started again after it
    failed."""
    if task_key == tasks.FOLDER_REVIEW:
        raise HTTPException(409, "The folder review is confirmed by building the folder.")
    if task_key not in tasks.TITLES:
        raise HTTPException(404, "There is no such task.")
    shipment = _shipment(session, shipment_id)
    task = tasks.get(session, shipment.id, task_key)
    by = body.by or "ops"
    title = tasks.TITLES[task_key]
    if task_key in tasks.AUTOMATIC:
        if task is None or task.status != tasks.FAILED:
            raise HTTPException(409, f"{title} runs by itself when its mail arrives. It can be started again only after it failed.")
        _complete_and_start(rt, session, shipment, task_key, by, allowed=(tasks.FAILED,))
        service.log(session, shipment.id, "task", f"{title}: {by} asked the browser agent to try again.")
        session.commit()
        return _one(rt, session, shipment)
    if task_key in tasks.APPROVED_BY_OPS:
        if task is None or task.status == tasks.PENDING:
            raise HTTPException(409, f"Nothing to approve yet. {task.note if task else ''}".strip())
        _complete_and_start(rt, session, shipment, task_key, by, allowed=(tasks.AWAITING_APPROVAL, tasks.FAILED))
        service.log(session, shipment.id, "task", f"{title}: approved by {by}. The browser agent is on it.")
        session.commit()
        return _one(rt, session, shipment)
    if task is None or task.status == tasks.PENDING:
        raise HTTPException(409, "Nothing to approve yet: build the DO folder first.")
    _complete_and_start(rt, session, shipment, task_key, by, allowed=(tasks.AWAITING_APPROVAL, tasks.FAILED))
    if rt.settings.odex_automated:
        service.log(session, shipment.id, "task", f"DO request on ODeX approved by {by}. The browser agent is raising it.")
        session.commit()
    return _one(rt, session, shipment)


@router.get("/shipments/{shipment_id}/runs", response_model=list[RunOut])
def list_runs(shipment_id: str, session: SessionDep) -> list[RunOut]:
    """The browser agent's runs for this shipment, newest first."""
    shipment = _shipment(session, shipment_id)
    rows = session.scalars(
        select(AutomationRun).where(AutomationRun.shipment_id == shipment.id).order_by(AutomationRun.started_at.desc())
    )
    return [
        RunOut(
            run_id=r.run_id,
            sop_name=r.sop_name,
            task_key=r.task_key,
            status=r.status,
            started_at=service.aware(r.started_at),
            completed_at=service.aware(r.completed_at),
            duration_seconds=r.duration_seconds,
            completed_steps=r.completed_steps,
            total_steps=r.total_steps,
            error_message=r.error_message,
            extractions=r.extractions or [],
        )
        for r in rows
    ]


@router.get("/shipments/{shipment_id}/portal")
def portal_records(shipment_id: str, session: SessionDep, rt: RuntimeDep) -> dict[str, Any]:
    """What the local portals hold for this shipment's B/L numbers: the invoice, the
    payments, the deposit and the DO request. Empty, and not available, when they don't run."""
    shipment = _shipment(session, shipment_id)
    bls = [b for b in [str(shipment.data.get("mbl_no") or ""), *split_list(shipment.data.get("hbl_nos"))] if b]
    try:
        reply = httpx.get(f"{rt.settings.local_portals_url}/control/records", params={"bl": bls}, timeout=3)
        reply.raise_for_status()
    except httpx.HTTPError:
        return {"available": False}
    return {"available": True, **reply.json()}


@router.get("/shipments/{shipment_id}/portal/files/{file_id}")
def portal_file(shipment_id: str, file_id: str, session: SessionDep, rt: RuntimeDep) -> Response:
    """A file the local portals made (invoice, receipt, slip, eDO), passed through so it
    opens on the tunnel too."""
    _shipment(session, shipment_id)
    if not file_id.isalnum():
        raise HTTPException(404, "No such file.")
    try:
        reply = httpx.get(f"{rt.settings.local_portals_url}/control/files/{file_id}", timeout=10)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "The local portals are not running. Start them with make portals.") from exc
    if reply.status_code != 200:
        raise HTTPException(404, "No such file.")
    return Response(reply.content, media_type="application/pdf", headers={"Content-Disposition": reply.headers.get("content-disposition", "inline")})


@router.get("/shipments/{shipment_id}/folder.pdf")
def download_folder(shipment_id: str, session: SessionDep, rt: RuntimeDep) -> FileResponse:
    shipment = _shipment(session, shipment_id)
    path = service.folder_pdf_path(rt, shipment)
    if path is None:
        raise HTTPException(404, "The DO folder has not been built yet.")
    if not path.is_file():
        raise HTTPException(404, "The built PDF is missing. Build the folder again.")
    return FileResponse(path, media_type="application/pdf", filename=shipment.folder_file)


@router.get("/shipments/{shipment_id}/events", response_model=list[EventOut])
def list_events(shipment_id: str, session: SessionDep) -> list[EventOut]:
    shipment = _shipment(session, shipment_id)
    rows = session.scalars(
        select(Event).where(Event.shipment_id == shipment.id).order_by(Event.at.desc(), Event.id.desc())
    )
    return [EventOut(at=service.aware(e.at), action=e.action, detail=e.detail) for e in rows]


@router.post("/demo/reset")
def reset_demo_data(session: SessionDep, rt: RuntimeDep) -> dict[str, bool]:
    seed_demo_data(session, rt.folder, rt.settings)
    return {"ok": True}
