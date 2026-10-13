"""Database models and session setup. Field values are stored as JSON so the
DO folder definition can change without a schema migration."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Profile(Base):
    """Fixed details used by every shipment: the forwarder, its signatory, the line's office."""

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Shipment(Base):
    """One master bill of lading, its house bills, and its DO folder."""

    __tablename__ = "shipments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_no: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="igm_filed")
    owner: Mapped[str | None] = mapped_column(String(120))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requested_on: Mapped[date | None] = mapped_column(Date)
    edo_received_on: Mapped[date | None] = mapped_column(Date)
    # Any change to fields or documents. A folder built before it is out of date.
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    folder_file: Mapped[str | None] = mapped_column(String(255))
    folder_built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Document(Base):
    """A file attached to a slot of the folder. Replaced files stay, with current=False."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)
    doc_key: Mapped[str] = mapped_column(String(60))
    hbl: Mapped[str | None] = mapped_column(String(60))
    current: Mapped[bool] = mapped_column(Boolean, default=True)
    original_name: Mapped[str] = mapped_column(String(255))
    original_file: Mapped[str] = mapped_column(String(255))  # exactly as uploaded
    pdf_file: Mapped[str] = mapped_column(String(255))  # the same, as a PDF
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")  # for matching identifiers
    checked_by_eye: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    """Audit trail: uploads, removals, checks, builds and stage moves."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")


def make_sessionmaker(database_url: str) -> sessionmaker:
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False
    engine = create_engine(database_url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
