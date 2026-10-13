"""HTTP API for the board, the shipment forms and DO set generation."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timezone
from typing import Annotated, Any

import jinja2
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import containers, documents
from .config import Settings
from .db import Client, Shipment, utcnow
from .do_set import DoSet
from .schemas import ClientOut, DocumentOut, MoveIn, PageProgressOut, ShipmentIn, ShipmentOut, ValuesIn
from .seed import CLIENT_ID, financial_year, seed_demo_data

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

router = APIRouter(prefix="/api")


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as session:
        yield session


def get_do_set(request: Request) -> DoSet:
    return request.app.state.do_set


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SessionDep = Annotated[Session, Depends(get_session)]
DoSetDep = Annotated[DoSet, Depends(get_do_set)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite drops time zones; stored times are UTC."""
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _client(session: Session) -> Client:
    client = session.get(Client, CLIENT_ID)
    if client is None:
        raise HTTPException(503, "There is no client profile yet. Reset the demo data or restart the API.")
    return client


def _shipment(session: Session, shipment_id: str) -> Shipment:
    shipment = session.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(404, "Shipment not found.")
    return shipment


def _check(do_set: DoSet, values: dict[str, Any], scopes: set[str]) -> None:
    errors = do_set.validate(values, scopes)
    if errors:
        raise HTTPException(422, {"message": "Some values could not be saved.", "fields": errors})


def _pages_done(do_set: DoSet, client: Client, shipment: Shipment) -> int:
    return sum(page.status == "done" for page in do_set.progress(client.data, shipment.data))


def _next_job_no(session: Session, prefix: str) -> str:
    base = f"{prefix}/{financial_year(date.today())}/"
    taken = session.scalars(select(Shipment.job_no).where(Shipment.job_no.startswith(base, autoescape=True)))
    numbers = [int(tail) for job_no in taken if (tail := job_no.removeprefix(base)).isdigit()]
    return f"{base}{max(numbers, default=0) + 1:04d}"


def client_out(client: Client) -> ClientOut:
    return ClientOut(id=client.id, name=client.name, values=client.data)


def shipment_out(shipment: Shipment, client: Client, do_set: DoSet) -> ShipmentOut:
    pages = do_set.progress(client.data, shipment.data)
    issues = {
        key: found
        for key, field in do_set.fields.items()
        if field.type == "containers" and (found := containers.problems(shipment.data.get(key)))
    }
    document = None
    if shipment.document_file:
        document = DocumentOut(
            url=f"/api/shipments/{shipment.id}/document",
            filename=shipment.document_file,
            generated_at=_aware(shipment.document_generated_at),
        )
    return ShipmentOut(
        id=shipment.id,
        job_no=shipment.job_no,
        status=shipment.status,
        owner=shipment.owner,
        values=shipment.data,
        pages=[PageProgressOut(id=p.id, filled=p.filled, total=p.total, status=p.status) for p in pages],
        pages_done=sum(p.status == "done" for p in pages),
        pages_total=len(pages),
        field_status=do_set.field_statuses(client.data, shipment.data),
        issues=issues,
        requested_on=shipment.requested_on,
        edo_received_on=shipment.edo_received_on,
        edo_valid_till=shipment.edo_valid_till,
        closed_on=shipment.closed_on,
        document=document,
        updated_at=_aware(shipment.updated_at),
    )


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/do-set")
def read_do_set(do_set: DoSetDep) -> dict[str, Any]:
    """Pages, fields and stages. The UI builds its forms from this."""
    return do_set.to_api()


@router.get("/client", response_model=ClientOut)
def read_client(session: SessionDep) -> ClientOut:
    return client_out(_client(session))


@router.patch("/client", response_model=ClientOut)
def update_client(body: ValuesIn, session: SessionDep, do_set: DoSetDep) -> ClientOut:
    _check(do_set, body.values, {"client"})
    client = _client(session)
    client.data = {**client.data, **body.values}
    session.commit()
    return client_out(client)


@router.get("/shipments", response_model=list[ShipmentOut])
def list_shipments(session: SessionDep, do_set: DoSetDep) -> list[ShipmentOut]:
    client = _client(session)
    rows = session.scalars(select(Shipment).order_by(Shipment.created_at.desc())).all()
    return [shipment_out(row, client, do_set) for row in rows]


@router.post("/shipments", response_model=ShipmentOut, status_code=201)
def create_shipment(body: ShipmentIn, session: SessionDep, do_set: DoSetDep, settings: SettingsDep) -> ShipmentOut:
    _check(do_set, body.values, {"shipment", "page"})
    client = _client(session)
    shipment = Shipment(
        id=uuid.uuid4().hex[:12],
        job_no=_next_job_no(session, settings.job_prefix),
        client_id=client.id,
        status="pending",
        owner=body.owner,
        data=dict(body.values),
    )
    session.add(shipment)
    session.commit()
    return shipment_out(shipment, client, do_set)


@router.get("/shipments/{shipment_id}", response_model=ShipmentOut)
def read_shipment(shipment_id: str, session: SessionDep, do_set: DoSetDep) -> ShipmentOut:
    return shipment_out(_shipment(session, shipment_id), _client(session), do_set)


@router.patch("/shipments/{shipment_id}/values", response_model=ShipmentOut)
def update_values(shipment_id: str, body: ValuesIn, session: SessionDep, do_set: DoSetDep) -> ShipmentOut:
    """Saves only the fields sent. The UI calls this as the user types."""
    _check(do_set, body.values, {"shipment", "page"})
    shipment = _shipment(session, shipment_id)
    shipment.data = {**shipment.data, **body.values}
    session.commit()
    return shipment_out(shipment, _client(session), do_set)


@router.post("/shipments/{shipment_id}/move", response_model=ShipmentOut)
def move_shipment(shipment_id: str, body: MoveIn, session: SessionDep, do_set: DoSetDep) -> ShipmentOut:
    shipment = _shipment(session, shipment_id)
    client = _client(session)
    if body.to != shipment.status:
        done, total = _pages_done(do_set, client, shipment), len(do_set.pages)
        if body.to != "pending" and done < total:
            raise HTTPException(
                409,
                f"Finish all {total} pages before moving this shipment past Details pending. "
                f"{done} of {total} are complete.",
            )
        today = date.today()
        if body.to == "requested" and shipment.requested_on is None:
            shipment.requested_on = today
        if body.to == "received" and shipment.edo_received_on is None:
            shipment.edo_received_on = today
        if body.to == "closed":
            shipment.closed_on = today
        shipment.status = body.to
        session.commit()
    return shipment_out(shipment, client, do_set)


@router.post("/shipments/{shipment_id}/generate", response_model=ShipmentOut)
def generate_do_set(shipment_id: str, session: SessionDep, do_set: DoSetDep, settings: SettingsDep) -> ShipmentOut:
    """Fills the template, keeps the file, and moves a Details pending shipment to Docs ready."""
    shipment = _shipment(session, shipment_id)
    client = _client(session)
    done, total = _pages_done(do_set, client, shipment), len(do_set.pages)
    if done < total:
        raise HTTPException(409, f"Finish all {total} pages before generating the DO set. {done} of {total} are complete.")
    context = documents.build_context(do_set, client.data, shipment.data, shipment.job_no)
    try:
        path = documents.render(settings.template_path, context, settings.output_dir, shipment.job_no)
    except documents.TemplateMissing as exc:
        raise HTTPException(409, str(exc)) from exc
    except jinja2.TemplateError as exc:
        raise HTTPException(409, f"The template has a placeholder problem: {exc}") from exc
    shipment.document_file = path.name
    shipment.document_generated_at = utcnow()
    if shipment.status == "pending":
        shipment.status = "ready"
    session.commit()
    return shipment_out(shipment, client, do_set)


@router.get("/shipments/{shipment_id}/document")
def download_document(shipment_id: str, session: SessionDep, settings: SettingsDep) -> FileResponse:
    shipment = _shipment(session, shipment_id)
    if not shipment.document_file:
        raise HTTPException(404, "No DO set has been generated for this shipment yet.")
    path = settings.output_dir / shipment.document_file
    if not path.is_file():
        raise HTTPException(404, "The generated file is missing. Generate the DO set again.")
    return FileResponse(path, media_type=DOCX_TYPE, filename=shipment.document_file)


@router.post("/demo/reset")
def reset_demo_data(session: SessionDep, do_set: DoSetDep, settings: SettingsDep) -> dict[str, bool]:
    seed_demo_data(session, do_set, settings.job_prefix)
    return {"ok": True}
