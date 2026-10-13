"""Request and response shapes for the API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

StageId = Literal["pending", "ready", "requested", "received", "closed"]


class ValuesIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ShipmentIn(BaseModel):
    owner: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class MoveIn(BaseModel):
    to: StageId


class PageProgressOut(BaseModel):
    id: str
    filled: int
    total: int
    status: Literal["done", "part", "empty"]


class DocumentOut(BaseModel):
    url: str
    filename: str
    generated_at: datetime | None


class ClientOut(BaseModel):
    id: str
    name: str
    values: dict[str, Any]


class ShipmentOut(BaseModel):
    id: str
    job_no: str
    status: StageId
    owner: str | None
    values: dict[str, Any]
    pages: list[PageProgressOut]
    pages_done: int
    pages_total: int
    field_status: dict[str, Literal["ok", "missing", "invalid"]]
    issues: dict[str, list[str]]
    requested_on: date | None
    edo_received_on: date | None
    edo_valid_till: date | None
    closed_on: date | None
    document: DocumentOut | None
    updated_at: datetime
