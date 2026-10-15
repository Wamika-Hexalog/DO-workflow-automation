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
    by: str | None = None  # who confirmed it; there is no login yet


class TaskCompleteIn(BaseModel):
    by: str | None = None  # who completed it; there is no login yet


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


class TaskOut(BaseModel):
    key: str
    title: str
    status: Literal["pending", "awaiting_approval", "completed", "failed"]
    note: str
    completed_at: datetime | None
    completed_by: str | None
    result: dict[str, Any]


class ProcessStepOut(BaseModel):
    """One step of the DO process for the job (app/process_steps.py)."""

    number: str
    title: str
    actor: Literal["app", "agent", "ops", "finance", "cma"]
    task_key: str | None
    state: Literal["done", "needs_you", "failed", "pending", "waiting", "not_started"]
    note: str
    at: datetime | None  # when it was done
    by: str | None
    waiting_on: Literal["ops", "finance", "cma"] | None
    since: datetime | None  # when the wait began: the clock


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
    building: bool  # ops confirmed the folder and the build is under way
    tasks: list[TaskOut]
    process: list[ProcessStepOut]
    requested_on: date | None
    edo_received_on: date | None
    updated_at: datetime


class EventOut(BaseModel):
    at: datetime
    action: str
    detail: str


class RunOut(BaseModel):
    run_id: str
    sop_name: str
    task_key: str | None
    status: Literal["running", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: int | None
    completed_steps: int
    total_steps: int
    error_message: str | None
    extractions: list[dict[str, Any]]
