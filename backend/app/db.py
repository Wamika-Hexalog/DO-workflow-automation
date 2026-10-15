"""Database models and session setup. Field values are stored as JSON so the
DO folder definition can change without a schema migration."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
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


class Task(Base):
    """Something ops have to do or approve, python-brain style. The workflows signal
    a task ready; completing it sends the event that starts the next workflow."""

    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("shipment_id", "task_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)
    task_key: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="pending")  # see tasks.STATUSES
    note: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    # When the status last changed: how long the step has waited for ops (the Process
    # page's clock). updated_at moves with every note, so it can't tell.
    status_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@event.listens_for(Task.status, "set")
def _stamp_status(task: Task, value: str, old: Any, _initiator: Any) -> None:
    if value != old:
        task.status_since = utcnow()


class IdempotencyKey(Base):
    """Insert-once claim taken before a side effect outside the app, such as raising
    a DO request on ODeX, so a replayed step can never do it twice."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    shipment_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(20), default="claimed")  # claimed, done, failed
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AutomationRun(Base):
    """One browser agent run: python-brain's AutomationRun, kept in SQLite."""

    __tablename__ = "automation_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shipment_id: Mapped[str | None] = mapped_column(String(32), index=True)
    task_key: Mapped[str | None] = mapped_column(String(60))
    sop_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running, completed, failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    total_steps: Mapped[int] = mapped_column(Integer, default=0)
    completed_steps: Mapped[int] = mapped_column(Integer, default=0)
    total_llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    browserbase_session_id: Mapped[str | None] = mapped_column(String(120))
    browserbase_live_url: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    extractions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    llm_decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    stagehand_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    self_healing_events: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


def make_sessionmaker(database_url: str) -> sessionmaker:
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False
    engine = create_engine(database_url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    _add_new_columns(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


# Columns added after a database was first made: create_all makes missing tables, not columns.
_NEW_COLUMNS = {"tasks": {"status_since": "DATETIME"}}


def _add_new_columns(engine: Any) -> None:
    for table, columns in _NEW_COLUMNS.items():
        have = {column["name"] for column in inspect(engine).get_columns(table)}
        with engine.begin() as connection:
            for name, sql_type in columns.items():
                if name not in have:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
