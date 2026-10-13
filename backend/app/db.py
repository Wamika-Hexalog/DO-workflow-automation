"""Database models and session setup. Field values are stored as JSON so the
DO set can change without a schema migration."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_no: Mapped[str] = mapped_column(String(40), unique=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    owner: Mapped[str | None] = mapped_column(String(120))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requested_on: Mapped[date | None] = mapped_column(Date)
    edo_received_on: Mapped[date | None] = mapped_column(Date)
    edo_valid_till: Mapped[date | None] = mapped_column(Date)
    closed_on: Mapped[date | None] = mapped_column(Date)
    document_file: Mapped[str | None] = mapped_column(String(255))
    document_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


def make_sessionmaker(database_url: str) -> sessionmaker:
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False
    engine = create_engine(database_url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
