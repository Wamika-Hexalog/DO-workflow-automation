"""Tasks for ops, after python-brain's Task Manager, kept in SQLite.

Workflows signal a task ready when there is something for ops to do. Ops complete
it in the app, which sends ``customs/task.completed``; the workflow waiting for that
task (or triggered by it) carries on. A signal never overwrites an ops decision:
a completed task stays completed until the workflow that owns it reopens it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Task, utcnow

PENDING = "pending"  # waiting on documents, fields or another step
AWAITING_APPROVAL = "awaiting_approval"  # ops have something to do
COMPLETED = "completed"
FAILED = "failed"  # the work after completion failed; ops can try again
STATUSES = (PENDING, AWAITING_APPROVAL, COMPLETED, FAILED)

FOLDER_REVIEW = "folder_review"
ODEX_DO_REQUEST = "odex_do_request"
TITLES = {
    FOLDER_REVIEW: "Review and build the DO folder",
    ODEX_DO_REQUEST: "Raise the DO request on ODeX",
}


class TaskConflict(Exception):
    """The task is not in a state that allows what was asked."""


def get(session: Session, shipment_id: str, task_key: str) -> Task | None:
    return session.scalar(select(Task).where(Task.shipment_id == shipment_id, Task.task_key == task_key))


def ensure(session: Session, shipment_id: str, task_key: str) -> Task:
    task = get(session, shipment_id, task_key)
    if task is None:
        task = Task(shipment_id=shipment_id, task_key=task_key, status=PENDING, note="", result={})
        session.add(task)
        session.flush()
    return task


def for_shipment(session: Session, shipment_id: str) -> list[Task]:
    return list(session.scalars(select(Task).where(Task.shipment_id == shipment_id).order_by(Task.id)))


def signal_ready(session: Session, shipment_id: str, task_key: str, note: str) -> bool:
    """Puts the task in front of ops. Returns False when it is already completed,
    so a replayed step never reopens work ops have done."""
    task = ensure(session, shipment_id, task_key)
    if task.status == COMPLETED:
        return False
    task.status = AWAITING_APPROVAL
    task.note = note
    return True


def set_waiting(session: Session, shipment_id: str, task_key: str, note: str) -> None:
    """Not ready for ops yet. Leaves a completed task alone."""
    task = ensure(session, shipment_id, task_key)
    if task.status != COMPLETED:
        task.status = PENDING
        task.note = note


def reopen(session: Session, shipment_id: str, task_key: str, *, ready: bool, note: str) -> None:
    """Takes back a completion because what it approved has changed."""
    task = ensure(session, shipment_id, task_key)
    task.status = AWAITING_APPROVAL if ready else PENDING
    task.note = note
    task.completed_at = None
    task.completed_by = None


def complete(
    session: Session, shipment_id: str, task_key: str, *, by: str, allowed: tuple[str, ...]
) -> dict[str, object]:
    """Marks the task done by ``by``. Returns what it was before, so the caller can
    put it back if the event that starts the next workflow cannot be sent."""
    task = ensure(session, shipment_id, task_key)
    if task.status not in allowed:
        raise TaskConflict(f"{TITLES.get(task_key, task_key)} is {task.status.replace('_', ' ')}.")
    before = {"status": task.status, "note": task.note, "completed_at": task.completed_at, "completed_by": task.completed_by}
    task.status = COMPLETED
    task.completed_at = utcnow()
    task.completed_by = by
    return before


def restore(session: Session, shipment_id: str, task_key: str, before: dict[str, object]) -> None:
    task = ensure(session, shipment_id, task_key)
    for name, value in before.items():
        setattr(task, name, value)


def fail(session: Session, shipment_id: str, task_key: str, note: str, result: dict | None = None) -> None:
    task = ensure(session, shipment_id, task_key)
    task.status = FAILED
    task.note = note
    if result is not None:
        task.result = result


def record_result(session: Session, shipment_id: str, task_key: str, note: str, result: dict) -> None:
    task = ensure(session, shipment_id, task_key)
    task.note = note
    task.result = result
