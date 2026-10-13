"""Request and response shapes for the API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

StageId = Literal["igm_filed", "invoice_paid", "deposit_approved", "folder_ready", "do_requested", "edo_received"]


class ValuesIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ShipmentIn(BaseModel):
    owner: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class MoveIn(BaseModel):
    to: StageId


class BuildIn(BaseModel):
    confirmed: bool = False  # the operator has reviewed every document


class ProfileOut(BaseModel):
    id: str
    name: str
    values: dict[str, Any]


class FlagOut(BaseModel):
    kind: Literal["missing", "fields", "duplicate", "mismatch", "unreadable", "setup"]
    message: str
    can_check: bool


class FileOut(BaseModel):
    id: str
    name: str
    pages: int
    uploaded_at: datetime
    checked_by_eye: bool
    original_url: str


class SlotOut(BaseModel):
    id: str
    doc_key: str
    hbl: str | None
    title: str
    source: Literal["fixed", "upload", "generate"]  # where the pages come from now
    defined_source: Literal["fixed", "upload", "generate"]  # as do_folder.yaml says
    stamp: str | None
    status: Literal["ok", "flagged", "missing"]
    flags: list[FlagOut]
    file: FileOut | None
    preview_url: str | None


class FolderOut(BaseModel):
    url: str
    filename: str
    built_at: datetime | None
    stale: bool


class ShipmentOut(BaseModel):
    id: str
    job_no: str
    status: StageId
    owner: str | None
    values: dict[str, Any]
    field_status: dict[str, Literal["ok", "missing", "invalid", "optional"]]
    issues: dict[str, list[str]]
    slots: list[SlotOut]
    docs_ok: int
    docs_total: int
    ready: bool
    folder: FolderOut | None
    requested_on: date | None
    edo_received_on: date | None
    updated_at: datetime


class EventOut(BaseModel):
    at: datetime
    action: str
    detail: str
